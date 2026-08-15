import copy
import os
import json
import traceback
import requests
import concurrent.futures
import zipfile
import urllib.request
import urllib.error

from logging import info, warning, debug, error, critical
from typing import Callable

from pcbnew import *


MODELS_DIR = "EASYEDA_MODELS"

# EasyEDA Standard (easyeda.com) endpoints. Parts from this source are prefixed with STD_PREFIX.
STD_PREFIX = "std:"
STD_SEARCH_URL = "https://easyeda.com/api/components/search"
STD_COMPONENT_URL = "https://easyeda.com/api/components/{uuid}"

# EasyEDA Standard uses units of 10 mil; KiCad models are in mm.
STD_UNIT_TO_MM = 10.0 / 39.37

# UUID strings can be in the format <uuid>|<owner_uuid>. This function gets the <uuid> part
def getUuidFirstPart(uuid):
    if not uuid:
        return None
    return uuid.split("|")[0]

# Split the user's part list into EasyEDA Pro codes/UUIDs and EasyEDA Standard UUIDs.
def splitSources(components):
    proComponents = []
    stdComponents = []

    for comp in components:
        comp = comp.strip()

        if not comp:
            continue

        if comp.startswith(STD_PREFIX):
            stdComponents.append(comp[len(STD_PREFIX):])
        else:
            proComponents.append(comp)

    return proComponents, stdComponents

# Field separators of the EasyEDA Standard shape format must not appear inside values.
def sanitizeStdValue(value):
    return str(value).replace("~", " ").replace("`", "'").replace("#@$", " ")

# Build a "LIB~x~y~params~rotation~~id~layer" shape wrapping all shapes of a Standard document,
# which is what KiCad's native EasyEDA Std importer enumerates as one symbol/footprint.
def buildStdLibShape(dataStr, extraParams, shapeId):
    head = dataStr.get("head") or {}
    params = dict(head.get("c_para") or {})
    params.update(extraParams)

    paramStr = "`".join(f"{sanitizeStdValue(k)}`{sanitizeStdValue(v)}" for k, v in params.items())
    root = "~".join(["LIB", str(head.get("x", 0)), str(head.get("y", 0)), paramStr, "", "", shapeId, "1"])

    return "#@$".join([root] + list(dataStr.get("shape") or []))

# Read a parameter (e.g. "package") out of an EasyEDA Standard "LIB~..." shape.
def stdShapeName(shape, key):
    root = shape.split("#@$")[0].split("~")

    if len(root) < 4:
        return None

    params = root[3].split("`")
    return dict(zip(params[0::2], params[1::2])).get(key)

class ComponentLoader():
    def __init__(self, kiprjmod, target_path, target_name, progress: Callable[[int, int], None], session: requests.Session):
        self.kiprjmod = kiprjmod
        self.target_path = target_path
        self.target_name = target_name
        self.progress = progress
        self.session = session

    def downloadAll(self, components):
        self.progress(0, 100)

        proComponents, stdComponents = splitSources(components)

        try:
            modelTasks = {}

            if proComponents:
                libDeviceFile, fetched_3dmodels = self.downloadSymFp(proComponents)
                modelTasks.update(self.collectProModels(libDeviceFile, fetched_3dmodels))

            if stdComponents:
                modelTasks.update(self.downloadStd(stdComponents))

            self.downloadModels(modelTasks)
            self.progress(100, 100)
            self.warnRescanNeeded()
        except Exception as e:
            traceback.print_exc()
            error(f"Failed to download components: {traceback.format_exc()}")

    def warnRescanNeeded(self):
        # KiCad's EasyEDA importers return a constant 0 from GetLibraryTimestamp(), and
        # FOOTPRINT_LIST_IMPL::ReadFootprintFiles skips the rescan whenever the generated
        # timestamp matches the cached one. A running pcbnew therefore never notices that
        # these libraries changed, so new footprints stay out of the footprint chooser.
        info("*****************************")
        info("Restart pcbnew to use new footprints: KiCad does not rescan EasyEDA")
        info("libraries while it is open, so the footprint chooser still shows the old list.")

    def downloadSymFp(self, components):
        info(f"Fetching info...")

        # Separate components into code-based and direct UUIDs
        code_components = []
        direct_uuids = []

        for comp in components:
            if comp.startswith("C"):
                code_components.append(comp)
            else:
                direct_uuids.append(comp)

        fetched_devices = {}

        # Fetch UUIDs from code-based components
        if code_components:
            resp = self.session.post("https://pro.easyeda.com/api/v2/devices/searchByCodes", data={"codes[]": code_components})
            resp.raise_for_status()
            found = resp.json()

            debug("searchByCodes: " + json.dumps(found, indent=4))

            if not found.get("success") or not found.get("result"):
                raise Exception(f"Unable to fetch device info: {found}")

            # Append fetched UUIDs to direct_uuids
            for entry in found["result"]:
                direct_uuids.append(entry['uuid'])

        # Fetch device info by UUID
        def fetch_device_info(dev_uuid):
            dev_info = self.session.get(f"https://pro.easyeda.com/api/devices/{dev_uuid}")
            dev_info.raise_for_status()

            debug("device info: " + json.dumps(dev_info.json(), indent=4))

            device = dev_info.json()["result"]
            fetched_devices[device["uuid"]] = device

        with concurrent.futures.ThreadPoolExecutor() as executor:
            for dev_uuid in direct_uuids:
                executor.submit(fetch_device_info, dev_uuid)

        # Collect symbol/footprint/3D model UUIDs to fetch
        fetched_symbols = {}
        fetched_footprints = {}
        fetched_3dmodels = {}
        uuid_to_obj_map = {}

        all_uuids = set()
        for entry in fetched_devices.values():
            if entry['attributes'].get('Symbol'):
                all_uuids.add(entry['attributes']['Symbol'])
                uuid_to_obj_map[entry['attributes']['Symbol']] = fetched_symbols

            if entry['attributes'].get('Footprint'):
                all_uuids.add(entry['attributes']['Footprint'])
                uuid_to_obj_map[entry['attributes']['Footprint']] = fetched_footprints

            if entry['attributes'].get('3D Model'):
                all_uuids.add(getUuidFirstPart(entry['attributes']['3D Model']))
                uuid_to_obj_map[getUuidFirstPart(entry['attributes']['3D Model'])] = fetched_3dmodels

        # Fetch symbols/footprints/3D models
        def fetch_component(uuid):
            url = f"https://pro.easyeda.com/api/v2/components/{uuid}"
            r = self.session.get(url)
            r.raise_for_status()
            return r.json()["result"]

        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {executor.submit(fetch_component, uuid): uuid for uuid in all_uuids}
            for future in concurrent.futures.as_completed(futures):
                try:
                    compData = future.result()
                    debug(f"Fetched component {json.dumps(compData, indent=4)}")

                    uuid_to_obj_map[compData["uuid"]][compData["uuid"]] = compData
                except Exception as e:
                    error(f"Failed to fetch component for uuid {futures[future]}: {e}")

        # Set symbol/footprint type fields
        for device in fetched_devices.values():
            if device['attributes'].get('Symbol'):
                fetched_symbols[device["attributes"]["Symbol"]]["type"] = device["symbol_type"]

            if device['attributes'].get('Footprint'):
                fetched_footprints[device["attributes"]["Footprint"]]["type"] = device["footprint_type"]

        # Extract dataStr
        footprint_data_str = {}
        symbol_data_str = {}

        # Separate dataStr for footprints
        for f_uuid, f_data in fetched_footprints.items():
            ds = self.extractDataStr(f_data)
            if ds:
                footprint_data_str[f_uuid] = ds

            f_data.pop("dataStr", None) # Remove the dataStr field if exists

        # Separate dataStr for symbols
        for s_uuid, s_data in fetched_symbols.items():
            ds = self.extractDataStr(s_data)
            if ds:
                symbol_data_str[s_uuid] = ds

            s_data.pop("dataStr", None) # Remove the dataStr field if exists

        libDeviceFile = {
            "devices": fetched_devices,
            "symbols": fetched_symbols,
            "footprints": fetched_footprints
        }

        os.makedirs(self.target_path, exist_ok=True)

        zip_filename = f"{self.target_path}/{self.target_name}.elibz"
        merged_data = copy.deepcopy(libDeviceFile)

        try:
            if os.path.exists(zip_filename):
                with zipfile.ZipFile(zip_filename, "r") as old_zip:
                    for name in old_zip.namelist():
                        if name == "device.json":
                            old_data = json.loads(old_zip.read("device.json").decode("utf-8"))
                            for entry_type in ["devices", "symbols", "footprints"]:
                                for key in old_data[entry_type]:
                                    if key not in merged_data[entry_type]:
                                        merged_data[entry_type][key] = old_data[entry_type][key]
                        if name.endswith('.esym'):
                            symbol_uuid = os.path.splitext(os.path.basename(name))[0]
                            if symbol_uuid not in symbol_data_str:
                                symbol_data_str[symbol_uuid] = old_zip.read(name).decode('utf-8')
                        elif name.endswith('.efoo'):
                            footprint_uuid = os.path.splitext(os.path.basename(name))[0]
                            if footprint_uuid not in footprint_data_str:
                                footprint_data_str[footprint_uuid] = old_zip.read(name).decode('utf-8')
        except Exception as e:
            warning(f"Failed to merge device.json data, overwriting: {e}")

        with zipfile.ZipFile(zip_filename, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("device.json", json.dumps(merged_data, indent=4))
            for fp_uuid, ds in footprint_data_str.items():
                zf.writestr(f"FOOTPRINT/{fp_uuid}.efoo", ds)
            for sym_uuid, ds in symbol_data_str.items():
                zf.writestr(f"SYMBOL/{sym_uuid}.esym", ds)

        info( "*****************************" )
        info(f"Downloaded {len(fetched_devices)} devices, {len(fetched_symbols)} symbols, {len(fetched_footprints)} footprints and added to library: {zip_filename}")
        return libDeviceFile, fetched_3dmodels

    # Collect 3D model download tasks for Pro devices: { model uuid: (target file, fit X mm, fit Y mm) }
    def collectProModels(self, libDeviceFile, fetched_3dmodels):
        modelTasks = {}

        debug("fetched_3dmodels: " + json.dumps(fetched_3dmodels, indent=4))
        debug("libDeviceFile: " + json.dumps(libDeviceFile, indent=4))

        for device in libDeviceFile["devices"].values():
            try:
                modelUuid = getUuidFirstPart(device["attributes"].get("3D Model"))

                if not modelUuid or modelUuid not in fetched_3dmodels:
                    info("No model for device '%s', footprint '%s'"
                         % (device.get("product_code", device.get("uuid")), 
                            device.get("footprint").get("display_title") if device.get("footprint") else "None"))
                    continue

                modelTitle = device["attributes"]["3D Model Title"]
                modelTransform = device["attributes"].get("3D Model Transform", "")

                dataStr = self.extractDataStr(fetched_3dmodels[modelUuid])

                if dataStr:
                    directUuid = json.loads(dataStr)["model"]
                else:
                    info("Unable to extract model for device '%s', footprint '%s'"
                         % (device.get("product_code", device.get("uuid")), 
                            device.get("footprint").get("display_title") if device.get("footprint") else "None"))
                    continue

                # Transform is in mils
                transform = [float(x) for x in modelTransform.split(",")]

                modelTasks[directUuid] = (self.modelFilePath(modelTitle),
                                          transform[0] / 39.37, transform[1] / 39.37)
            except KeyboardInterrupt:
                return modelTasks
            except Exception as e:
                traceback.print_exc()
                info("Cannot get model for device '%s': %s" % (device.get("product_code", device.get("uuid")), str(e)))
                continue

        return modelTasks

    def modelFilePath(self, modelTitle):
        # KiCad's EasyEDA importers always look for models in ${KIPRJMOD}/EASYEDA_MODELS
        return os.path.normpath(os.path.join(self.kiprjmod, MODELS_DIR, modelTitle + ".step"))

    # modelTasks: { model uuid: (target file, fit X mm, fit Y mm) }
    def downloadModels(self, modelTasks):
        self.totalToDownload = 0
        self.downloadedCounter = 0
        self.statExisting = 0
        self.statDownloaded = 0
        self.statSkipped = 0
        self.statFailed = 0

        info( "*****************************" )
        info(f"Loading 3D models...")
        self.progress(0, 100)

        with concurrent.futures.ThreadPoolExecutor(1) as texecutor:
            def fixupModel(fixTaskArgs):
                directUuid, kfilePath, fitXmm, fitYmm = fixTaskArgs

                file_name = os.path.splitext( os.path.basename( kfilePath ) ) [0]
                jfilePath = kfilePath + "_jlc"

                debug( "Loading STEP model %s" % (file_name) )
                model: UTILS_STEP_MODEL = UTILS_STEP_MODEL.LoadSTEP(jfilePath)

                if not model:
                    error( "Error loading model '%s'" % (file_name) )
                    return
                
                debug( "Converting STEP model '%s'" % (file_name) )
                bbox: UTILS_BOX3D = model.GetBoundingBox()

                try:
                    if fitXmm and fitYmm:
                        bsize: VECTOR3D = bbox.GetSize()
                        scaleFactorX = fitXmm / bsize.x;
                        scaleFactorY = fitYmm / bsize.y;
                        scaleFactor = ( scaleFactorX + scaleFactorY ) / 2

                        debug( "Dimensions %f %f factors %f %f avg %f model '%s'" %
                            (fitXmm, fitYmm, scaleFactorX, scaleFactorY, scaleFactor, file_name) )

                        if abs( scaleFactorX - scaleFactorY ) > 0.1:
                            warning( "Scale factors do not match: X %.3f; Y %.3f for model '%s'." %
                                (scaleFactorX, scaleFactorY, file_name) )
                            warning( "**** The model '%s' might be misoriented! ****" % (file_name) )
                        elif abs( scaleFactor - 1.0 ) > 0.01:
                            warning( "Scaling '%s' by %f" % (file_name, scaleFactor) )
                            model.Scale( scaleFactor );
                        else:
                            debug( "No scaling for %s" % (file_name) )

                except Exception as e:
                    traceback.print_exc()
                    error( "Error scaling model '%s': %s" % (file_name, str(e)) )
                    return

                newbbox          = model.GetBoundingBox()
                center: VECTOR3D = newbbox.GetCenter()

                model.Translate( -center.x, -center.y, -newbbox.Min().z )

                debug( "Saving STEP model %s" % (file_name) )
                model.SaveSTEP( kfilePath )

                # Delete the temporary JLC file after successful conversion
                try:
                    if os.path.exists(jfilePath):
                        os.remove(jfilePath)
                        debug(f"Deleted temporary file {jfilePath}")
                except Exception as e:
                    info(f"Failed to delete temporary file {jfilePath}: {str(e)}")
            with concurrent.futures.ThreadPoolExecutor(8) as dexecutor:
                def downloadStep(dnlTaskArgs):
                    directUuid, (kfilePath, fitXmm, fitYmm) = dnlTaskArgs
                    file_name = os.path.splitext( os.path.basename( kfilePath ) ) [0]

                    try:
                        if os.path.exists(kfilePath):
                            info("Skipping '%s': STEP model file already exists." % (file_name))
                            self.statExisting += 1
                        else:
                            stepUrlFormat = "https://modules.easyeda.com/qAxj6KHrDKw4blvCG8QJPs7Y/{uuid}"
                            jfilePath = kfilePath + "_jlc"
                            url = stepUrlFormat.format(uuid=directUuid)

                            debug("Downloading '%s'" % (file_name))
                            debug("'%s' from '%s'" % (file_name, url))
                            os.makedirs(os.path.dirname(kfilePath), exist_ok=True)

                            try:
                                urllib.request.urlretrieve(url, jfilePath)
                            except urllib.error.HTTPError as e:
                                if e.code == 404:
                                    # EasyEDA stores this part's 3D body only as OBJ; KiCad's
                                    # native importer hardcodes a .step path, so it can't be used.
                                    if os.path.exists(jfilePath):
                                        os.remove(jfilePath)

                                    info("No STEP model for '%s' (EasyEDA only provides OBJ); "
                                         "KiCad needs STEP, skipping." % (file_name))
                                    self.statSkipped += 1
                                else:
                                    raise
                            else:
                                if os.path.isfile(jfilePath):
                                    debug("Downloaded '%s'." % (file_name))
                                    self.statDownloaded += 1

                                    fixTaskArgs = [directUuid, kfilePath, fitXmm, fitYmm]
                                    texecutor.submit(fixupModel, fixTaskArgs)
                                else:
                                    warning( "Path '%s' is not a file." % jfilePath )
                                    self.statFailed += 1

                    except Exception as e:
                        warning("Failed to download model '%s': %s" % (file_name, str(e)))
                        self.statFailed += 1

                    self.downloadedCounter += 1
                    self.progress(self.downloadedCounter, self.totalToDownload)

                self.totalToDownload = len(modelTasks)
                dexecutor.map(downloadStep, modelTasks.items())

        info( "" )
        info( "*****************************" )
        info( "          All done.          " )
        info( "*****************************" )
        info( "" )
        info( "Total model count: %d" % len(modelTasks) )
        info( "STEP models downloaded: %d" % self.statDownloaded )
        info( "Already existing models: %d" % self.statExisting )
        info( "Skipped (OBJ-only, no STEP): %d" % self.statSkipped )
        info( "Failed downloads: %d" % self.statFailed )
        self.progress(100, 100)

    # Download EasyEDA Standard components into "<target_name>-std.zip", which KiCad's native
    # "EasyEDA (JLCEDA) Std" importers read directly. Returns 3D model download tasks.
    def downloadStd(self, uuids):
        info( "*****************************" )
        info(f"Fetching EasyEDA Std components...")

        fetched = {}

        def fetchComponent(uuid):
            resp = self.session.get(STD_COMPONENT_URL.format(uuid=uuid))
            resp.raise_for_status()
            found = resp.json()

            debug("std component: " + json.dumps(found, indent=4))

            if not found.get("success") or not found.get("result"):
                raise Exception(f"Unable to fetch component info: {found}")

            fetched[uuid] = found["result"]

        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {executor.submit(fetchComponent, uuid): uuid for uuid in uuids}
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    error(f"Failed to fetch EasyEDA Std component {futures[future]}: {e}")

        symbolShapes = {}
        footprintShapes = {}
        layers = []
        modelTasks = {}

        for uuid, component in fetched.items():
            dataStr = component.get("dataStr")

            if not dataStr:
                warning(f"Component {uuid} has no document data, skipping.")
                continue

            head = dataStr.get("head") or {}
            params = head.get("c_para") or {}

            # EasyEDA Std serves symbols (docType 2) and standalone PCB footprints
            # (docType 4) from the same endpoint. Footprints have no packageDetail:
            # the document itself is the footprint.
            if str(head.get("docType")) == "4":
                packageName = params.get("package") or component.get("title") or uuid
                layers = layers or dataStr.get("layers") or []
                footprintShapes[packageName] = buildStdLibShape(dataStr, {"package": packageName},
                                                                f"gge{len(footprintShapes) + 1}")
                modelTasks.update(self.collectStdModels(dataStr))
                continue

            symbolName = params.get("name") or component.get("title") or uuid
            symbolShapes[symbolName] = buildStdLibShape(dataStr, {"spiceSymbolName": symbolName},
                                                        f"gge{len(symbolShapes) + 1}")

            package = component.get("packageDetail")

            if not package or not package.get("dataStr"):
                info(f"Component '{symbolName}' has no footprint document."
                     f" A standalone footprint may be listed separately in the search results.")
                continue

            fpDataStr = package["dataStr"]
            fpParams = (fpDataStr.get("head") or {}).get("c_para") or {}
            packageName = fpParams.get("package") or package.get("title") or package.get("uuid")

            layers = layers or fpDataStr.get("layers") or []
            footprintShapes[packageName] = buildStdLibShape(fpDataStr, {"package": packageName},
                                                            f"gge{len(footprintShapes) + 1}")
            modelTasks.update(self.collectStdModels(fpDataStr))

        if not symbolShapes and not footprintShapes:
            raise Exception("No EasyEDA Std components could be loaded.")

        zip_filename = self.writeStdLibrary(symbolShapes, footprintShapes, layers)

        info( "*****************************" )
        info(f"Downloaded {len(symbolShapes)} symbols and {len(footprintShapes)} footprints "
             f"from EasyEDA Std into library: {zip_filename}")

        return modelTasks

    # Collect 3D model tasks from the "outline3D" SVGNODE of an EasyEDA Std footprint.
    def collectStdModels(self, fpDataStr):
        modelTasks = {}

        for shape in fpDataStr.get("shape") or []:
            if not shape.startswith("SVGNODE~"):
                continue

            try:
                attrs = json.loads(shape.split("~", 1)[1]).get("attrs") or {}

                if attrs.get("c_etype") != "outline3D" or not attrs.get("uuid") or not attrs.get("title"):
                    continue

                modelTasks[attrs["uuid"]] = (self.modelFilePath(attrs["title"]),
                                             float(attrs["c_width"]) * STD_UNIT_TO_MM,
                                             float(attrs["c_height"]) * STD_UNIT_TO_MM)
            except Exception as e:
                warning(f"Failed to read 3D model reference: {e}")

        return modelTasks

    # Write symbols and footprints as two EasyEDA Std documents in one zip, merging existing entries.
    def writeStdLibrary(self, symbolShapes, footprintShapes, layers):
        os.makedirs(self.target_path, exist_ok=True)
        zip_filename = f"{self.target_path}/{self.target_name}-std.zip"

        def mergeOld(name, shapes, nameKey, getShapeList):
            try:
                with zipfile.ZipFile(zip_filename, "r") as old_zip:
                    if name not in old_zip.namelist():
                        return

                    for oldShape in getShapeList(json.loads(old_zip.read(name).decode("utf-8"))):
                        oldName = stdShapeName(oldShape, nameKey)

                        # Freshly downloaded entries replace the old ones with the same name
                        if oldName and oldName not in shapes:
                            shapes[oldName] = oldShape
            except Exception as e:
                warning(f"Failed to merge existing EasyEDA Std library, overwriting: {e}")

        if os.path.exists(zip_filename):
            mergeOld("symbols.json", symbolShapes, "spiceSymbolName",
                     lambda doc: doc["schematics"][0]["dataStr"]["shape"])
            mergeOld("footprints.json", footprintShapes, "package", lambda doc: doc["shape"])

        canvas = "CA~1000~1000~#FFFFFF~yes~#CCCCCC~10~1000~1000~line~1~pixel~5~0~0"

        symbolDoc = {
            "docType": 5,
            "title": self.target_name,
            "schematics": [{
                "docType": 1,
                "title": self.target_name,
                "dataStr": {
                    "head": {"docType": "1", "x": 0, "y": 0},
                    "canvas": canvas,
                    "shape": list(symbolShapes.values())
                }
            }]
        }

        footprintDoc = {
            "head": {"docType": "3", "x": 0, "y": 0, "c_para": {}},
            "canvas": canvas,
            "layers": layers,
            "shape": list(footprintShapes.values())
        }

        with zipfile.ZipFile(zip_filename, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("symbols.json", json.dumps(symbolDoc))
            zf.writestr("footprints.json", json.dumps(footprintDoc))

        return zip_filename

    # Extract dataStr from component data. If dataStr is not available, try to decrypt and decompress the data from dataStrId URL.
    def extractDataStr(self, component_data):
        if not component_data:
            return None
            
        # Try direct dataStr first
        dataStr = component_data.get("dataStr")
        if dataStr:
            return dataStr
            
        # Try dataStrId if dataStr not available
        dataStrId = component_data.get("dataStrId")
        if dataStrId:
            try:
                keyHex = component_data.get("key")
                ivHex = component_data.get("iv")

                debug("dataStrId key: " + keyHex)
                debug("dataStrId iv: " + ivHex)
                
                dataStrResp = self.session.get(dataStrId)
                dataStrResp.raise_for_status()

                debug("dataStrId encrypted content: " + dataStrResp.content.hex())
                
                from . import decryptor
                decryptedStr = decryptor.decryptDataStrIdData(dataStrResp.content, keyHex, ivHex)

                debug("dataStrId decrypted content: " + decryptedStr)

                return decryptedStr
            except Exception as e:
                info(f"Failed to fetch/decrypt dataStrId: {e}")
                
        return None
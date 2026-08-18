import copy
import hashlib
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

# EasyEDA Pro (pro.easyeda.com) endpoints.
PRO_COMPONENT_URL = "https://pro.easyeda.com/api/v2/components/{uuid}"
PRO_DEVICE_URL = "https://pro.easyeda.com/api/devices/{uuid}"
PRO_SEARCH_URL = "https://pro.easyeda.com/api/v2/devices/search"
PRO_SEARCH_BY_CODES_URL = "https://pro.easyeda.com/api/v2/devices/searchByCodes"

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

# EasyEDA Pro returns HTTP 200 with `{"success": false, "code": …, "message": …}` on
# failure, so `data["result"]` KeyErrors instead of explaining itself. Raise the API's
# own message instead of a bare `'result'`.
def proResult(data):
    if not data.get("success") or not data.get("result"):
        raise Exception(f"{data.get('message') or data} (code {data.get('code')})")
    return data["result"]

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
def stdShapeParams(shape):
    root = shape.split("#@$")[0].split("~")

    if len(root) < 4:
        return {}

    params = root[3].split("`")
    return dict(zip(params[0::2], params[1::2]))


def stdShapeName(shape, key):
    return stdShapeParams(shape).get(key)


# A version of this plugin that could not tell docType 2 from 4 wrapped standalone
# footprint documents as symbols. A footprint document's c_para has no `name`, and the
# wrapper took `spiceSymbolName` from the document title, which for a footprint is its
# package - a combination no real symbol produces, since EasyEDA names every symbol.
def isMisfiledFootprint(shape):
    params = stdShapeParams(shape)
    package = params.get("package")

    return bool(package) and not params.get("name") and params.get("spiceSymbolName") == package


def entryTitle(entry):
    return entry.get("display_title") or entry.get("title") or ""


# KiCad enumerates an .elibz from device.json and then loads each entry by name, so an
# entry whose document is not in the zip becomes a footprint that appears in the chooser
# and fails to load - which aborts the scan of the whole library, hiding every other part
# in it. Entries like that are left behind whenever a document could not be read, so the
# library has to be pruned to what it actually contains on every write.
def pruneOrphans(libDeviceFile, symbolDocs, footprintDocs):
    dropped = 0

    for entry_type, docs in (("symbols", symbolDocs), ("footprints", footprintDocs)):
        for uuid in [u for u in libDeviceFile[entry_type] if u not in docs]:
            warning(f"Dropping {entry_type[:-1]} '{entryTitle(libDeviceFile[entry_type][uuid])}'"
                    " from the library index: its document is missing, and KiCad stops"
                    " reading a library at the first entry it cannot load.")
            del libDeviceFile[entry_type][uuid]
            dropped += 1

    # A device pointing at a dropped document cannot be placed either.
    for uuid in [u for u, d in libDeviceFile["devices"].items()
                 if (d.get("attributes") or {}).get("Symbol") not in libDeviceFile["symbols"]
                 or (d.get("attributes") or {}).get("Footprint") not in libDeviceFile["footprints"]]:
        warning(f"Dropping device '{entryTitle(libDeviceFile['devices'][uuid])}'"
                " from the library index: its symbol or footprint document is missing.")
        del libDeviceFile["devices"][uuid]
        dropped += 1

    return dropped


# KiCad names a footprint after its device.json title, so two documents sharing a title
# leave one of them unreachable. The uuid that sorts first keeps the plain name, which
# keeps whatever a board already references resolving to the same document.
def uniquifyTitles(entries):
    seen = {}

    for uuid in sorted(entries):
        title = entryTitle(entries[uuid])

        if title not in seen:
            seen[title] = uuid
            continue

        unique = f"{title} ({uuid[:4]})"
        warning(f"Renaming a second '{title}' to '{unique}': KiCad can only reach one"
                " document per name.")
        entries[uuid]["display_title"] = unique
        seen[unique] = uuid


# KiCad names a Pro footprint after its package ("SOT-23_L2.9-W1.3-P1.90-LS2.4-BR"), and the
# importer sets no description or keywords, so the chooser can only match that string. Nobody
# searches for a package they did not choose: they search for the part number. Give every
# device's footprint a second entry named after the part, pointing at a copy of the same
# document. The package entry keeps its name so boards already placing it still resolve, and
# one shared package document is never labelled with whichever part happened to arrive first.
#
# The copy needs a device of its own: the importer takes a footprint's 3D model from whichever
# device references that footprint uuid, so an alias with no device would place without a body.
# Devices are invisible in both choosers - eeschema enumerates "symbols" and pcbnew
# "footprints" - so the extra entry adds no duplicate anywhere the user looks.
#
# Aliases are rebuilt from scratch on every write rather than merged, because merging is what
# let them self-replicate: an alias device has the same part name as its source, so a second
# pass aliased the alias, and every uniquify rename broke the title check and seeded another.
# Each real device gets one deterministic alias (uuid = md5(deviceUuid, name)), marked
# ALIAS_MARKER so a later pass can drop and rebuild it identically instead of growing.
ALIAS_MARKER = "_jlc_alias"


def aliasUuid(key, name):
    return hashlib.md5(f"{key}:{name}".encode("utf-8")).hexdigest()


def addPartAliases(libDeviceFile, footprintDocs, customAliases=None):
    footprints = libDeviceFile["footprints"]
    devices = libDeviceFile["devices"]
    customAliases = customAliases or {}

    def partName(device):
        attributes = device.get("attributes") or {}
        return attributes.get("Manufacturer Part") or device.get("product_code") or ""

    partNames = {partName(device) for device in devices.values()}
    partNames.discard("")

    def partNamed(title):
        return any(title == name or title.startswith(name + " (") for name in partNames)

    def aliasNames(device, deviceUuid):
        """Names to file this device's footprint under: the part number, the human
        description (LCSC Part Name), the symbol document's name, and whatever alias
        the user typed in the queue - deduplicated and non-empty. The queue alias is
        matched by code or by uuid."""
        attributes = device.get("attributes") or {}
        symbolName = (device.get("symbol") or {}).get("display_title", "")
        names = []

        for name in (partName(device),
                     attributes.get("LCSC Part Name", ""),
                     symbolName,
                     customAliases.get(deviceUuid, ""),
                     customAliases.get(device.get("product_code") or "", "")):
            name = (name or "").strip()

            if name and name not in names:
                names.append(name)

        return names

    # Drop the aliases this pass created last time, marked so they are unambiguous.
    for uuid in [u for u, entry in footprints.items() if entry.get(ALIAS_MARKER)]:
        footprints.pop(uuid, None)
        footprintDocs.pop(uuid, None)
    for uuid in [u for u, device in devices.items() if device.get(ALIAS_MARKER)]:
        devices.pop(uuid, None)

    # Drop unmarked aliases left by the first version of this feature: a device copy has the
    # same display_title as its source and points at a part-named footprint.
    byTitle = {}
    for uuid, device in devices.items():
        byTitle.setdefault(entryTitle(device), []).append(uuid)
    for uuid in [u for u, device in devices.items()
                 if len(byTitle[entryTitle(device)]) > 1
                 and partNamed(entryTitle(footprints.get((device.get("attributes") or {}).get("Footprint"), {})))]:
        devices.pop(uuid, None)

    # A part-named footprint no surviving device references only ever served a copy.
    referenced = {(device.get("attributes") or {}).get("Footprint") for device in devices.values()}
    for uuid in [u for u in footprints
                 if u not in referenced and partNamed(entryTitle(footprints[u]))]:
        footprints.pop(uuid, None)
        footprintDocs.pop(uuid, None)

    # One deterministic alias per name a part is known by.
    added = 0
    for deviceUuid, device in list(devices.items()):
        footprintUuid = (device.get("attributes") or {}).get("Footprint")

        if footprintUuid not in footprintDocs:
            continue

        entry = footprints.get(footprintUuid)

        if entry is None:
            continue

        for name in aliasNames(device, deviceUuid):
            if entryTitle(entry) == name:
                continue

            uuid = aliasUuid(deviceUuid, name)

            if uuid in footprints:
                continue

            alias = copy.deepcopy(entry)
            alias["uuid"] = uuid
            alias["display_title"] = name
            alias["title"] = name.lower()
            alias[ALIAS_MARKER] = True
            footprints[uuid] = alias
            footprintDocs[uuid] = footprintDocs[footprintUuid]

            aliasDevice = copy.deepcopy(device)
            aliasDevice["uuid"] = aliasUuid(deviceUuid, name + ":dev")
            aliasDevice["attributes"]["Footprint"] = uuid
            aliasDevice[ALIAS_MARKER] = True
            devices[aliasDevice["uuid"]] = aliasDevice
            added += 1

    return added


class ComponentLoader():
    def __init__(self, kiprjmod, target_path, target_name, progress: Callable[[int, int], None], session: requests.Session):
        self.kiprjmod = kiprjmod
        self.target_path = target_path
        self.target_name = target_name
        self.progress = progress
        self.session = session

    # Downloads everything and returns what reached the library, so the caller can show an
    # outcome instead of making the user read the log:
    # { symbols, footprints, models, skipped, failed, error }
    def downloadAll(self, components, aliases=None):
        self.progress(0, 100)

        proComponents, stdComponents = splitSources(components)
        summary = {"symbols": 0, "footprints": 0, "models": 0, "skipped": 0, "failed": 0,
                   "error": None}

        try:
            modelTasks = {}
            fetchFailures = 0

            if proComponents:
                libDeviceFile, fetched_3dmodels, proSymbols, proFootprints, proFailed = \
                    self.downloadSymFp(proComponents, aliases)
                summary["symbols"] += proSymbols
                summary["footprints"] += proFootprints
                fetchFailures += proFailed
                modelTasks.update(self.collectProModels(libDeviceFile, fetched_3dmodels))

            if stdComponents:
                stdTasks, stdSymbols, stdFootprints = self.downloadStd(stdComponents)
                summary["symbols"] += stdSymbols
                summary["footprints"] += stdFootprints
                modelTasks.update(stdTasks)

            self.downloadModels(modelTasks)
            summary["models"] = self.statDownloaded + self.statExisting
            summary["skipped"] = self.statSkipped
            summary["failed"] = self.statFailed + fetchFailures
            self.progress(100, 100)
            self.warnRescanNeeded()
        except Exception as e:
            traceback.print_exc()
            error(f"Failed to download components: {traceback.format_exc()}")
            summary["error"] = str(e) or e.__class__.__name__

        return summary

    def warnRescanNeeded(self):
        # KiCad's EasyEDA importers return a constant 0 from GetLibraryTimestamp(), and
        # FOOTPRINT_LIST_IMPL::ReadFootprintFiles skips the rescan whenever the generated
        # timestamp matches the cached one. A running pcbnew therefore never notices that
        # these libraries changed, so new footprints stay out of the footprint chooser.
        info("*****************************")
        info("Restart pcbnew to use new footprints: KiCad does not rescan EasyEDA")
        info("libraries while it is open, so the footprint chooser still shows the old list.")

    def downloadSymFp(self, components, aliases=None):
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

            device = proResult(dev_info.json())
            fetched_devices[device["uuid"]] = device

        failed = 0

        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {executor.submit(fetch_device_info, dev_uuid): dev_uuid
                       for dev_uuid in direct_uuids}

            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    failed += 1
                    error(f"Failed to fetch device {futures[future]}: {e}")

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
            return proResult(r.json())

        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {executor.submit(fetch_component, uuid): uuid for uuid in all_uuids}
            for future in concurrent.futures.as_completed(futures):
                try:
                    compData = future.result()
                    debug(f"Fetched component {json.dumps(compData, indent=4)}")

                    uuid_to_obj_map[compData["uuid"]][compData["uuid"]] = compData
                except Exception as e:
                    failed += 1
                    error(f"Failed to fetch component {futures[future]}: {e}")

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
            else:
                warning(f"Footprint {f_uuid} has no readable document and is left out of the library.")

            f_data.pop("dataStr", None) # Remove the dataStr field if exists

        # Separate dataStr for symbols
        for s_uuid, s_data in fetched_symbols.items():
            ds = self.extractDataStr(s_data)
            if ds:
                symbol_data_str[s_uuid] = ds
            else:
                warning(f"Symbol {s_uuid} has no readable document and is left out of the library.")

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

        pruneOrphans(merged_data, symbol_data_str, footprint_data_str)
        addPartAliases(merged_data, footprint_data_str, aliases)
        uniquifyTitles(merged_data["footprints"])

        with zipfile.ZipFile(zip_filename, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("device.json", json.dumps(merged_data, indent=4))
            for fp_uuid, ds in footprint_data_str.items():
                zf.writestr(f"FOOTPRINT/{fp_uuid}.efoo", ds)
            for sym_uuid, ds in symbol_data_str.items():
                zf.writestr(f"SYMBOL/{sym_uuid}.esym", ds)

        # Count what actually reached the zip: a device whose document could not
        # be read is in device.json but has no symbol or footprint to load.
        written_symbols = len(symbol_data_str.keys() & fetched_symbols.keys())
        written_footprints = len(footprint_data_str.keys() & fetched_footprints.keys())

        info( "*****************************" )
        info(f"Downloaded {len(fetched_devices)} devices, {written_symbols} symbols, "
             f"{written_footprints} footprints and added to library: {zip_filename}")
        return libDeviceFile, fetched_3dmodels, written_symbols, written_footprints, failed

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
                named = params.get("package")

                if named:
                    # The symbol names its footprint and EasyEDA sent no such
                    # document. Older user parts often reference a footprint that was
                    # never published, and then the symbol imports with a Footprint
                    # field pointing at nothing - silently, until placement.
                    warning(f"Component '{symbolName}' references footprint '{named}',"
                            f" which EasyEDA did not return; the symbol will import"
                            f" without one. Search for '{named}' to see whether it"
                            f" exists as a part of its own.")
                else:
                    info(f"Component '{symbolName}' has no footprint document."
                         f" A standalone footprint may be listed separately in the"
                         f" search results.")

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

        return modelTasks, len(symbolShapes), len(footprintShapes)

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

        def mergeOld(name, shapes, nameKey, getShapeList, dropMisfiled=False):
            try:
                with zipfile.ZipFile(zip_filename, "r") as old_zip:
                    if name not in old_zip.namelist():
                        return

                    for oldShape in getShapeList(json.loads(old_zip.read(name).decode("utf-8"))):
                        oldName = stdShapeName(oldShape, nameKey)

                        if not oldName:
                            continue

                        # Left behind by a version that filed footprint documents as
                        # symbols; the merge kept resurrecting them, so the symbol
                        # library still lists footprints that cannot be placed.
                        if dropMisfiled and isMisfiledFootprint(oldShape):
                            warning(f"Dropped '{oldName}' from the symbol library: it is a"
                                    " footprint that an older version filed as a symbol."
                                    " Download it again to get it as a footprint.")
                            continue

                        # Freshly downloaded entries replace the old ones with the same name
                        if oldName not in shapes:
                            shapes[oldName] = oldShape
            except Exception as e:
                warning(f"Failed to merge existing EasyEDA Std library, overwriting: {e}")

        if os.path.exists(zip_filename):
            mergeOld("footprints.json", footprintShapes, "package", lambda doc: doc["shape"])
            mergeOld("symbols.json", symbolShapes, "spiceSymbolName",
                     lambda doc: doc["schematics"][0]["dataStr"]["shape"], dropMisfiled=True)

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

    def extractDataStr(self, component_data):
        return extractDataStr(self.session, component_data)

# Extract dataStr from component data. If dataStr is not available, try to decrypt and decompress the data from dataStrId URL.
def extractDataStr(session, component_data):
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

            dataStrResp = session.get(dataStrId)
            dataStrResp.raise_for_status()

            debug("dataStrId encrypted content: " + dataStrResp.content.hex())

            try:
                from . import decryptor
            except ImportError:
                # Also importable as loose modules, e.g. from a test harness.
                import decryptor

            decryptedStr = decryptor.decryptDataStrIdData(dataStrResp.content, keyHex, ivHex)

            debug("dataStrId decrypted content: " + decryptedStr)

            return decryptedStr
        except Exception as e:
            # Loud: the part is left without its document, so the library entry
            # would be unusable and the user has to know why.
            warning(f"Failed to fetch/decrypt dataStrId: {e}")

    return None

# Fetch a Pro component document and return its dataStr, decrypting when needed.
def fetchDataStr(session, uuid):
    if not uuid:
        return None

    resp = session.get(PRO_COMPONENT_URL.format(uuid=getUuidFirstPart(uuid)))
    resp.raise_for_status()

    return extractDataStr(session, resp.json().get("result"))
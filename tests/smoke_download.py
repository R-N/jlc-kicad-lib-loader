"""Download real parts and check KiCad's own importers can read the result.

    python3 tests/smoke_download.py

Needs the network and KiCad's `pcbnew`. Run it by hand after touching the
download path; `test_offline.py` covers the logic, this covers the contract with
KiCad, which is the part that has actually broken before.

Nothing is written outside a temporary directory.
"""

import json
import logging
import os
import sys
import tempfile
import zipfile

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pcbnew  # noqa: E402  (after sys.path)

from component_loader import MODELS_DIR, ComponentLoader  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# C2040 = RP2040, a Pro part with an LCSC code and a 3D model.
PRO_PART = "C2040"
# ESP32-WROOM module, an EasyEDA Std user-contributed part with a 3D model.
STD_PART = "std:ec1f5c172d024b6bb2383ffb1f290fe3"
# A second Std part, downloaded on its own afterwards to prove the merge.
STD_EXTRA = "std:4c0dae4e58984c06b7812642e521e379"

kiprjmod = tempfile.mkdtemp(prefix="jlcsmoke")
target = os.path.join(kiprjmod, "EasyEDA_Lib")
session = requests.Session()
session.headers.update({"User-Agent": "jlc-kicad-lib-loader/test"})


def loader():
    return ComponentLoader(kiprjmod=kiprjmod, target_path=target, target_name="EasyEDA_Lib",
                           progress=lambda done, total: None, session=session)


# Pro and Std in one run: they share the model downloader and the lib tables.
# The summary is what the dialog reports, so it must count what actually landed.
summary = loader().downloadAll([PRO_PART, STD_PART])
print("summary:", summary)
assert not summary["error"], f"download reported an error: {summary['error']}"
assert summary["symbols"] == 2 and summary["footprints"] == 2, f"miscounted: {summary}"
assert not summary["failed"], f"parts failed: {summary}"

elibz = os.path.join(target, "EasyEDA_Lib.elibz")
stdzip = os.path.join(target, "EasyEDA_Lib-std.zip")
assert os.path.exists(elibz), "no .elibz was written"
assert os.path.exists(stdzip), "no -std.zip was written"

with zipfile.ZipFile(elibz) as z:
    device = json.loads(z.read("device.json"))
    entries = z.namelist()

codes = [d.get("product_code") for d in device["devices"].values()]
print("pro devices:", codes, "symbols:", len(device["symbols"]),
      "footprints:", len(device["footprints"]))
assert PRO_PART in codes, f"{PRO_PART} missing from device.json"
assert any(n.startswith("SYMBOL/") for n in entries), "no .esym in the .elibz"
assert any(n.startswith("FOOTPRINT/") for n in entries), "no .efoo in the .elibz"

with zipfile.ZipFile(stdzip) as z:
    stdNames = z.namelist()
    stdSymbols = json.loads(z.read("symbols.json"))["schematics"][0]["dataStr"]["shape"]
    stdFootprints = json.loads(z.read("footprints.json"))

print("std zip:", stdNames, "symbols:", len(stdSymbols),
      "footprints:", len(stdFootprints["shape"]))
assert stdSymbols, "no symbols in the Std library"
assert stdFootprints["shape"], "no footprints in the Std library"
assert stdFootprints["layers"], "Std footprints have no layer palette"

# STEP models always land under $KIPRJMOD/EASYEDA_MODELS, whatever the library path.
models = os.path.join(kiprjmod, MODELS_DIR)
files = sorted(os.listdir(models)) if os.path.isdir(models) else []
print("models:", files)
assert files, "no 3D models were converted"
for name in files:
    path = os.path.join(models, name)
    assert name.endswith(".step"), f"unexpected model file {name}"
    assert os.path.getsize(path) > 1000, f"{name} is too small to be a STEP body"
    assert not os.path.exists(path + "_jlc"), f"temp download left behind for {name}"

# The real test: KiCad's own importers must read what we wrote.
pro = pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.EASYEDAPRO)
std = pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.EASYEDA)

proFootprints = list(pro.FootprintEnumerate(elibz))
stdFootprintNames = list(std.FootprintEnumerate(stdzip))
print("KiCad Pro footprints:", proFootprints)
print("KiCad Std footprints:", stdFootprintNames)
assert proFootprints, "KiCad found no footprints in the .elibz"
assert stdFootprintNames, "KiCad found no footprints in the -std.zip"

for plugin, path, name in ((pro, elibz, proFootprints[0]), (std, stdzip, stdFootprintNames[0])):
    footprint = plugin.FootprintLoad(path, name)
    assert footprint, f"{name} did not load"
    pads = footprint.Pads()
    box = footprint.GetBoundingBox()
    print(f"loaded {name}: {len(pads)} pads, "
          f"{pcbnew.ToMM(box.GetWidth()):.1f} x {pcbnew.ToMM(box.GetHeight()):.1f} mm")
    assert len(pads) > 0, f"{name} has no pads"
    assert box.GetWidth() > 0 and box.GetHeight() > 0, f"{name} has no extent"

# Downloading another part must not throw away what is already in the library.
loader().downloadAll([STD_EXTRA])
after = list(std.FootprintEnumerate(stdzip))
print("after merge:", after)
assert set(stdFootprintNames) <= set(after), "re-downloading dropped existing footprints"
assert len(after) > len(stdFootprintNames), "the extra part added nothing"

print("DOWNLOAD SMOKE OK")

# JLCPCB/LCSC Library Loader

This KiCad plugin allows you to search and download symbols/footprints with 3D models to a local .elibz library that can be read by KiCad.

![image](https://github.com/user-attachments/assets/37e16749-94ea-46e8-88c9-e85164eaf495)

# System support

- **KiCad**: version 8.0.7 or newer.
- **Windows**: version 10 or newer with normal KiCad installation.
- **Ubuntu**: install KiCad from PPA. To make the preview work, install `python3-wxgtk-webview4.0`.
- **Flatpak**: works but preview is not available due to missing webkitgtk2.
- **macOS**: works (Python 3.9+)

# Installation

1. Download the latest `jlc-kicad-lib-loader-*-pcm.zip` archive from [Releases page](https://github.com/dsa-t/jlc-kicad-lib-loader/releases).

2. Open PCM in KiCad, click "Install from File...", then choose the downloaded `-pcm` archive:

   ![image](https://github.com/user-attachments/assets/debae118-1292-498a-81f2-29fdc2cf455d)

## To support importing encrypted data

### Windows

3. Open "KiCad x.x Command Prompt":

   ![image](https://github.com/user-attachments/assets/9975de9a-d1cc-4ee7-94b8-11fb492b8b77)

4. Execute `pip install pycryptodome`

   ![image](https://github.com/user-attachments/assets/1abcd9ed-7358-4508-a9fb-75d2bc9bb2a1)

### Debian/Ubuntu

```
sudo apt install python3-pycryptodome
```

### Flatpak

```
flatpak run --command=pip org.kicad.KiCad install pycryptodome
```

### Other OSes

```
pip install pycryptodome
```

### Mac OS

KiCad does NOT use the system Python (/usr/bin/python3).
It comes with a built-in Python located inside the application.

The path to Python KiCad:

```
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3

```

Installing pycryptodome

```
KPY="/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
"$KPY" -V
"$KPY" -m pip install --user pycryptodome
```
Checking the result
```
"$KPY" -c "from Crypto.Cipher import AES; print('pycryptodome OK')"
```
If you see:
```
pycryptodome OK
```
everything is set up correctly.

#### Installing certifi

On macOS, Python inside KiCad does not see the system certificate store, which causes the following error:

```
SSL: CERTIFICATE_VERIFY_FAILED
```
Installing certifi
```
"$KPY" -m pip install --user --upgrade certifi
```

Check:
```
"$KPY" -c "import certifi; print(certifi.where())"
```



# Library sources

The "Source" dropdown next to the search box selects where parts come from:

| Source | Site | Written to |
| --- | --- | --- |
| All Sources / JLC System / JLC Public | EasyEDA Pro (`pro.easyeda.com`) | `<LibName>.elibz` |
| EasyEDA Std Public | EasyEDA Standard (`easyeda.com`), user-contributed libraries | `<LibName>-std.zip` |

EasyEDA Std parts are listed and downloaded by UUID, shown as `std:<uuid>` in the parts list; you
can also paste such lines manually. They are stored in a separate file because KiCad reads them
with its native "EasyEDA (JLCEDA) Std" importers, and are registered as a separate library named
`<LibName>_Std` in the project library tables. Both sources can be downloaded in one run.

Symbols and footprints are separate documents on EasyEDA Std. A symbol may carry an inline
footprint, or none at all; a footprint on its own (no symbol) is also downloadable and lands in
the footprint library. If a symbol reports no footprint, search for the footprint by name.

3D models of both sources go to `EASYEDA_MODELS` in the project directory.

If you add the `-std.zip` library by hand instead, select the library type explicitly:
"EasyEDA (JLCEDA) Std" for symbols and "EasyEDA / JLCEDA Std" for footprints — KiCad's automatic
file-type guess does not recognize a `.zip` as an EasyEDA Std library.

## Searching and previewing

Search by keyword, or paste a UUID to look up a single EasyEDA Std part directly. Results list
the contributor of each user-contributed library, so you can judge a part before importing it.

Selecting a result previews its symbol and footprint drawings side by side, plus the part's
parameters. Drawings come from EasyEDA's own renderings where they exist. EasyEDA only renders
parts that carry an LCSC part number, so for JLC Public parts drawn from scratch the plugin
draws the symbol and footprint itself from the part's own document. Parts whose EasyEDA document
is empty — placeholders and title-block entries, of which the public library holds a fair few —
say so instead of showing a blank pane. Use the "Open in EasyEDA Pro"/"Open in EasyEDA Std" link
for the interactive viewer.

# Library setup

The plugin now automatically manages library configuration:

- **Library Name Storage**: The library name is saved in a `jlc-kicad-lib-loader.ini` file in your project directory and will be remembered for future use.
- **Automatic Library Table Addition**: When downloading components, if the library is not found in your project-specific Symbol/Footprint library tables, the plugin will prompt you to add it automatically.

## Manual Library Setup (if needed)

If you need to manually add the .elibz library to your Symbol/Footprint library tables:

![image](https://github.com/user-attachments/assets/45583737-6747-4aa8-975c-2a90a6f192d6)

### Symbol library table:

![image](https://github.com/user-attachments/assets/a3ff3856-5637-46da-8349-0b965986680f)

### Footprint library table:

![image](https://github.com/user-attachments/assets/8512a77f-95e5-4d4f-bba6-4a2b5660e218)

# Development

Every `.py` in the repository root is copied into the plugin archive as-is, so scratch and
test scripts belong in `tests/`. Build an installable archive with `./create_pcm_archive.sh`
(it writes `out/jlc-kicad-lib-loader-<version>-pcm.zip`, installable through PCM's
"Install from File…"). The dialog is generated by wxFormBuilder 4.2.1 from
`easyeda_lib_loader_dialog.fbp`; edit the `.fbp`, not the generated `.py`.

Tests are plain `assert` scripts, run directly:

```
python3 tests/test_offline.py     # pure logic, no network, always safe to run
python3 tests/smoke_download.py   # downloads real parts, then has KiCad read them back
python3 tests/smoke_preview.py    # opens the dialog and checks each preview branch
```

The two smoke scripts need the network and KiCad's Python (`pcbnew`, and `wx` with WebView
for the preview one); they write only to temporary directories.

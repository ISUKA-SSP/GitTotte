# Bundled components

GitTotte's portable package contains the original application, a Python runtime, Tcl/Tk,
the PyInstaller bootloader, and the upstream MinGit distribution when enabled.

- Python: https://www.python.org/ — license text is copied into `licenses`.
- Tcl/Tk: https://www.tcl.tk/ — available license terms are copied into `licenses`.
- PyInstaller: https://pyinstaller.org/ — GPL with the PyInstaller bootloader exception;
  its available license text is copied into `licenses`.
- MinGit / Git for Windows: https://gitforwindows.org/mingit.html — upstream files and
  their notices are preserved under `tools/git`.
  Pinned binary release: https://github.com/git-for-windows/git/releases/tag/v2.56.0.windows.4
  Git source for the corresponding tag: https://github.com/git-for-windows/git/tree/v2.56.0.windows.4
  Build/distribution scripts and component sources: https://github.com/git-for-windows/build-extra

The complete MinGit archive is extracted without removing its component files or notices.
Exact package versions and the downloaded archive's SHA-256 are recorded in `build-info.json` and `tools/git/etc/package-versions.txt`.

The complete source packages corresponding to every package in that version list,
including their PKGBUILD files, patches and upstream source repositories/archives,
are provided in `GitTotte-1.1.2-ThirdPartySources.zip` on the same release page:
https://github.com/ISUKA-SSP/GitTotte/releases/tag/v1.1.2 .
The package-to-source mapping and SHA-256 values are in `source-lock.json` inside
that archive. `SOURCES.txt` accompanies the application and points to this source
bundle. The application source/build scripts are also attached as
`GitTotte-1.1.2-Source.zip`.

The source bundle includes the exact official PyInstaller 6.22.3 source archive
used to rebuild the unmodified bootloader. GitTotte's MIT license and the
character-image permission do not change any bundled component's license.

## Embedded PowerShell panel

- pywinpty 3.1.2 (MIT): https://github.com/andfoy/pywinpty/tree/v3.1.2
  Windows ConPTY interface; includes Rust components from winpty-rs and PyO3.
  Distributed wheel notices are copied into `licenses/pywinpty`.
- pyte 0.8.2 (LGPLv3): https://github.com/selectel/pyte/tree/0.8.2
  The unmodified library source is included in `_internal/pyte` as separate,
  replaceable Python files, outside the frozen application archive. You may replace
  those files with a compatible modified version; modifying/reverse engineering
  GitTotte for debugging modifications to this library is permitted.
  The license text is included in `licenses/pyte`; complete GPL-3.0 and LGPL-3.0 texts are also in `licenses`. The original pyte 0.8.2 source distribution is included in the third-party source bundle. Application build scripts and
  source are available at https://github.com/ISUKA-SSP/GitTotte .
- wcwidth 0.2.13 (MIT): https://github.com/jquast/wcwidth/tree/0.2.13
  Its available license is included in `licenses/wcwidth`.

- pywin32 311 (PSF-based license): https://github.com/mhammond/pywin32/releases/tag/b311
  Windows Shell recycle-bin operation. Available license files are included in `licenses/pywin32`.

## Application and character images

GitTotte's original code and documentation are covered by the root `LICENSE`
(MIT). The character images use the permission in `ASSET_LICENSE.md`.
Project-credit links requested in the README and asset notice are voluntary.

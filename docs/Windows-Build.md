# Windows build

Build on Windows with 64-bit Python 3.10 or later and Inno Setup 7. The
v1.4.0 release used Python 3.10.11, PyInstaller 6.18.0 and Inno Setup 7.1.1.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm KeanSeatsCatcher.spec
ISCC.exe KeanSeatsCatcher.iss
```

`dist\KeanSeatsCatcher.exe` is the complete Portable build. The installer
packages that same executable. `VERSION` supplies the version shown in About
and in the Windows file properties of both executables.

Setup defaults to an all-users install. Run it with `/CURRENTUSER` for a
per-user install. Both modes store KSC data in the account that runs the app.

## Source and licenses

The [KSC source](https://github.com/Daozhu1007/KeanSeatsCatcher/tree/v1.4.0)
and build scripts are available at the release tag. The original project code
is offered under the [MIT License](../LICENSE). The combined Windows app is
distributed under the [GNU GPL v3](https://www.gnu.org/licenses/gpl-3.0.html)
with the bundled Qt bindings and widgets. License notices are included in
the executable archive.

| Component in the v1.4.0 build | Upstream source |
| --- | --- |
| PyQt6 6.10.2 | [PyQt6 source distribution](https://pypi.org/project/PyQt6/6.10.2/#files) |
| Qt 6.10.2 | [Qt source archives](https://download.qt.io/archive/qt/6.10/6.10.2/single/) |
| PyQt6-Fluent-Widgets 1.11.1 | [QFluentWidgets source distribution](https://pypi.org/project/PyQt6-Fluent-Widgets/1.11.1/#files) |
| PyQt6-Frameless-Window 0.8.2 | [Frameless Window source distribution](https://pypi.org/project/PyQt6-Frameless-Window/0.8.2/#files) |
| PyQt6-sip 13.13.1 | [SIP source distribution](https://pypi.org/project/PyQt6-sip/13.13.1/#files) |
| Python 3.10.11 | [CPython source](https://github.com/python/cpython/tree/v3.10.11) |
| Selenium 4.43.0 | [Selenium source](https://github.com/SeleniumHQ/selenium/tree/selenium-4.43.0) |
| PyInstaller 6.18.0 | [PyInstaller source](https://github.com/pyinstaller/pyinstaller/tree/v6.18.0) |
| Inno Setup 7.1.1 | [Inno Setup source](https://github.com/jrsoftware/issrc/tree/is-7_1_1) |

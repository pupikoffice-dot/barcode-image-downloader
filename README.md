# Barcode Image Downloader

Internal desktop tool: paste barcodes, preview product images, save JPEGs.

The app checks [GitHub Releases](https://github.com/pupikoffice-dot/barcode-image-downloader/releases) on launch and offers **Update now** or **Later** when a newer `.exe` is published.
Updates are verified (HTTPS from github.com, exact size, SHA-256 digest published by GitHub) before they replace the running copy.

## Release

```
python release.py
```

Builds `dist/Barcode Image Downloader.exe` from the spec and publishes it as release `v<APP_VERSION>` (version lives in `updater.py`).

## When Windows blocks the app

The exe is not code-signed, so Windows treats it as coming from an unknown publisher. What employees see, and what to do:

| What appears | Cause | Fix |
|---|---|---|
| Blue **"Windows protected your PC"** | SmartScreen: file was downloaded with a browser/email/chat and has no publisher reputation | Click **More info → Run anyway**. Or right-click the exe → **Properties** → tick **Unblock** → OK. Once is enough. Updates installed by the app itself never show this. |
| **"Part of this app has been blocked"** / app silently refuses to start on a new Windows 11 PC | **Smart App Control** (Windows Security → App & browser control). It blocks every unsigned app, with no per-app exception | Only code signing fixes this (see below). Turning Smart App Control off works but cannot be turned back on without reinstalling Windows, so don't do it casually. |
| Exe disappears / "Threat found" / Trojan:Win32/Wacatac or similar | Defender or another antivirus false positive on the PyInstaller loader | Restore from Protection history, then submit the exe as a false positive at https://www.microsoft.com/wdsi/filesubmission (choose *Incorrectly detected*). Microsoft usually clears it within a day or two. IT can also allow the folder the app lives in. |
| Company antivirus (not Defender) quarantines it | Same false positive, different vendor | Ask IT to allowlist the exe (by folder or SHA-256 from the release page), or submit to that vendor's false-positive form. |

### What the app already does to avoid being flagged (v1.1.0+)

- No UPX packing (a top antivirus trigger).
- Real Windows version info (company, product, version) embedded in the exe.
- Never asks for admin rights.
- The updater no longer writes or runs a `.bat` script. It renames the running exe and moves the new one into place, which is the same approach browsers use. The old ping/copy/delete batch loop looked like malware to behaviour-based antivirus.

### The real fix: code signing

A signed exe shows a named publisher, passes Smart App Control, and builds SmartScreen reputation for every future release. Options:

- **Azure Artifact Signing** (formerly Trusted Signing), about $10/month. Check eligibility for your country/entity type first.
- **OV code-signing certificate** from a CA (Certum, Sectigo, SSL.com …), roughly $100–400/year, delivered on a hardware token or cloud HSM.

Once you have one, sign `dist/Barcode Image Downloader.exe` with `signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 ...` before `gh release create` in `release.py`.

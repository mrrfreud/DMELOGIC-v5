Set WinScriptHost = CreateObject("WScript.Shell")
WinScriptHost.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""C:\DMELOGIC-v5\tools\fax_downloader\run_fax_downloader.ps1""", 0, False

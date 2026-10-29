@echo off
set CSC_PATH=C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe

if not exist "%CSC_PATH%" (
    echo [!] Nie znaleziono kompilatora CSC pod sciezka: %CSC_PATH%
    pause
    exit /b 1
)

echo [*] Kompilacja HOI4_Commander.exe...
"%CSC_PATH%" /target:winexe /win32icon:hoi4_trainer.ico /out:HOI4_Commander.exe Launcher.cs

if %ERRORLEVEL% equ 0 (
    echo [+] Pomyslnie skompilowano HOI4_Commander.exe!
) else (
    echo [!] Blad kompilacji.
)

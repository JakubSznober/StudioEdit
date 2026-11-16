@echo off
rem Logowanie GitHub CLI (przenosny gh w tools\gh). Przegladarka otworzy sie sama - wklej kod i zatwierdz.
set GH="%~dp0..\tools\gh\bin\gh.exe"
echo.| %GH% auth login --hostname github.com --git-protocol https --web
%GH% auth setup-git
%GH% auth status

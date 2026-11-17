@echo off
rem Weekly refresh: download the shops, update Discogs prices, publish the encrypted data to the web page.
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist out mkdir out
echo ==== %date% %time% ==== >> out\refresh.log
"C:\Users\DELL\AppData\Local\Programs\Python\Python312\python.exe" -m pipeline.run --xlsx >> out\refresh.log 2>&1
if errorlevel 1 (
  echo Refresh FAILED - see out\refresh.log
  echo FAILED >> out\refresh.log
  exit /b 1
)
git add state.enc docs/data.enc >> out\refresh.log 2>&1
git diff --cached --quiet || git commit -q -m "Weekly data refresh" >> out\refresh.log 2>&1
git push -q >> out\refresh.log 2>&1
echo Refresh finished. The web page updates within a minute or two.
echo done >> out\refresh.log

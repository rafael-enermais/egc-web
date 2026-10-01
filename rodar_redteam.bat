@echo off
rem Roda o teste de seguranca (red team) do Erik.AI. Basta dar 2 cliques neste arquivo.
rem Precisa de: pip install anthropic pandas   (uma vez so)
cd /d "%~dp0app"
if "%ANTHROPIC_API_KEY%"=="" set /p ANTHROPIC_API_KEY=Cole a chave da Anthropic (sk-ant-...) e tecle Enter: 
python redteam_erik.py --rodar
echo.
echo Relatorio gerado em: %CD%\relatorio_redteam_erik.md
pause

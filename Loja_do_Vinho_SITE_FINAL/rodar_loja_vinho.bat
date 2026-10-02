@echo off
cd /d "%~dp0"
set LOJA_VINHO_DB=%~dp0pdv_local.db
set LOJA_VINHO_HOST=127.0.0.1
set LOJA_VINHO_PORT=8787
set LOJA_VINHO_PIX_KEY=74988154699
set LOJA_VINHO_ADMIN_TOKEN=troque-este-token-por-um-seguro
python api_loja_vinho.py
pause

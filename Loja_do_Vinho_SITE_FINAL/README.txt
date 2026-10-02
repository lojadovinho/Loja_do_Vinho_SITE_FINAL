LOJA DO VINHO — PACOTE DE TESTE

1) Faça uma cópia de segurança do pdv_local.db antes de qualquer teste.
2) Instale as dependências:
   pip install pillow qrcode
3) Edite rodar_loja_vinho.bat e troque LOJA_VINHO_ADMIN_TOKEN por um token forte.
4) Execute rodar_loja_vinho.bat.
5) No navegador abra:
   http://127.0.0.1:8787/
   http://127.0.0.1:8787/admin.html

IMPORTANTE:
- A API está configurada para 127.0.0.1 por segurança.
- O banco usado pelo teste é o pdv_local.db da pasta.
- Não exponha o SQLite diretamente na internet.
- Para publicar o site e manter o PDV no PC, será necessário definir uma ponte HTTPS segura entre o site público e esta API.
- O pedido online NÃO baixa estoque quando o cliente apenas envia o pedido. A baixa acontece somente quando o administrador confirma o pagamento.

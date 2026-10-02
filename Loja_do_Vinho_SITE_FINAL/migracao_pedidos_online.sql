CREATE TABLE IF NOT EXISTS pedidos_online (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'AGUARDANDO_PAGAMENTO',
    cliente TEXT,
    endereco TEXT,
    bairro TEXT,
    frete REAL NOT NULL DEFAULT 0,
    desconto REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL DEFAULT 0,
    forma_pagamento TEXT DEFAULT 'PIX',
    itens_json TEXT NOT NULL,
    venda_id INTEGER,
    observacao TEXT,
    FOREIGN KEY (venda_id) REFERENCES vendas(id)
);
CREATE INDEX IF NOT EXISTS idx_pedidos_online_status ON pedidos_online(status);
CREATE INDEX IF NOT EXISTS idx_pedidos_online_criado ON pedidos_online(criado_em);

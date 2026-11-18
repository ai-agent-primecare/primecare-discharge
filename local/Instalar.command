#!/bin/bash
# Duplo clique (uma vez só) para instalar o que o script precisa neste Mac.
cd "$(dirname "$0")"
python3 -m pip install --user -r requirements.txt && echo && echo "Pronto. Usa o Google Chrome já instalado — nada mais para baixar."
read -p "Aperte Enter para fechar…"

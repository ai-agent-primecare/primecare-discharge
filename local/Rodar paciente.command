#!/bin/bash
# Duplo clique: pergunta o MRN e faz o pacote completo.
cd "$(dirname "$0")"
read -p "MRN do paciente: " MRN
python3 primecare_run.py patient "$MRN"
read -p "Aperte Enter para fechar…"

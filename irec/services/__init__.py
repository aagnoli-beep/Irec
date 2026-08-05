"""Servizi applicativi: orchestrano dominio e adapter.

Livello intermedio fra `api/` e il resto: qui vive il coordinamento
(chi chiamare, in che ordine, cosa persistere), mentre le REGOLE stanno
in `domain/` e l'IO negli `adapters/`. Direzione delle dipendenze:
api → services → (domain, adapters); mai il contrario.
"""

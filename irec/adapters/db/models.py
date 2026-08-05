"""Schema del database di IREC (entità del PRD 2.1).

Regole di questo modulo:
- OGNI tabella di dati porta `tenant_id`: è la chiave dell'isolamento
  multi-tenant, il valore arriva sempre dal call-token (mai dal client).
- OGNI foreign key è composita `(tenant_id, id)`: il database rende
  impossibile un arco fra tenant diversi, così l'isolamento non dipende
  dalla disciplina del chiamante e una cancellazione a cascata non può
  attraversare il confine.
- Gli importi sono `Numeric(14, 2)`, mai float: il dominio è finanziario.
- `audit_log` è append-only: nessuna riga viene aggiornata o cancellata
  se non per cancellazione GDPR dell'intero tenant.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from irec.domain.enums import (
    Canale,
    OriginePagamento,
    Pacchetto,
    StatoComunicazione,
    StatoFattura,
    StatoPosizione,
    TipoEvento,
)

IMPORTO = Numeric(14, 2)
ID = String(32)  # uuid4().hex
TENANT_ID = String(64)  # claim tenant_id del call-token
OPERATORE = String(64)
DENOMINAZIONE = String(255)


def enum_col(enum_cls: type[StrEnum]) -> Enum:
    """Colonna di enum come VARCHAR con CHECK sui valori ammessi.

    `native_enum=False` evita i tipi ENUM di Postgres (che andrebbero
    migrati a ogni nuovo valore) e restituisce in lettura membri
    dell'enum, non stringhe; `create_constraint` e `validate_strings`
    fanno rifiutare i valori fuori enum dal database e da Python.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=32,
        values_callable=lambda e: [membro.value for membro in e],
    )


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


def tenant_fk(
    tabella: str, colonna: str, *, ondelete: str = "CASCADE"
) -> ForeignKeyConstraint:
    """Foreign key composita `(tenant_id, <colonna>)` verso `(tenant_id, id)`.

    È ciò che impedisce a livello di database che una riga referenzi
    un'entità di un altro tenant.
    """
    return ForeignKeyConstraint(
        ["tenant_id", colonna],
        [f"{tabella}.tenant_id", f"{tabella}.id"],
        ondelete=ondelete,
    )


def tenant_pk(nome: str) -> UniqueConstraint:
    """Chiave referenziabile dalle FK composite."""
    return UniqueConstraint("tenant_id", "id", name=f"uq_{nome}_tenant_id")


class Base(DeclarativeBase):
    pass


class TenantScoped:
    """Mixin: chiave primaria, tenant e timestamp di creazione."""

    __tablename__: str

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(TENANT_ID, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Mandante(TenantScoped, Base):
    """La PMI cliente di Irec. Un tenant = un mandante."""

    __tablename__ = "mandante"
    __table_args__ = (
        tenant_pk("mandante"),
        UniqueConstraint("tenant_id", name="uq_mandante_tenant"),
    )

    ragione_sociale: Mapped[str] = mapped_column(DENOMINAZIONE)
    partita_iva: Mapped[str] = mapped_column(String(20))
    pacchetto: Mapped[Pacchetto] = mapped_column(enum_col(Pacchetto))
    alias_email: Mapped[str | None] = mapped_column(String(255))
    credit_manager_id: Mapped[str | None] = mapped_column(String(64))

    clienti: Mapped[list["ClienteFinale"]] = relationship(
        back_populates="mandante", cascade="all, delete-orphan"
    )


class ClienteFinale(TenantScoped, Base):
    """Il debitore. Non è utente della piattaforma: riceve solo comunicazioni."""

    __tablename__ = "cliente_finale"
    __table_args__ = (
        tenant_pk("cliente_finale"),
        tenant_fk("mandante", "mandante_id"),
        UniqueConstraint("tenant_id", "piva_cf", name="uq_cliente_tenant_piva"),
    )

    mandante_id: Mapped[str] = mapped_column(ID, index=True)
    denominazione: Mapped[str] = mapped_column(DENOMINAZIONE)
    piva_cf: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255))
    pec: Mapped[str | None] = mapped_column(String(255))
    telefono: Mapped[str | None] = mapped_column(String(32))
    # Canali disattivati su richiesta del cliente (opt-out, PRD 5.2):
    # valori di `Canale` serializzati come stringhe.
    canali_opt_out: Mapped[list[str]] = mapped_column(JSON, default=list)

    mandante: Mapped[Mandante] = relationship(back_populates="clienti")
    posizioni: Mapped[list["Posizione"]] = relationship(
        back_populates="cliente", cascade="all, delete-orphan"
    )


class Posizione(TenantScoped, Base):
    """Aggregato delle fatture di uno stesso cliente finale (PRD 2.1)."""

    __tablename__ = "posizione"
    __table_args__ = (
        tenant_pk("posizione"),
        tenant_fk("cliente_finale", "cliente_id"),
        # Referenziabile da Fattura per impedire che la fattura sia
        # attribuita a un cliente diverso da quello della sua posizione.
        UniqueConstraint("tenant_id", "id", "cliente_id", name="uq_posizione_cliente"),
    )

    cliente_id: Mapped[str] = mapped_column(ID, index=True)
    stato: Mapped[StatoPosizione] = mapped_column(
        enum_col(StatoPosizione), default=StatoPosizione.APERTA
    )

    cliente: Mapped[ClienteFinale] = relationship(back_populates="posizioni")
    fatture: Mapped[list["Fattura"]] = relationship(
        back_populates="posizione", cascade="all, delete-orphan"
    )


class Fattura(TenantScoped, Base):
    """Il documento su cui si ancora il timing dei solleciti (scadenza = T)."""

    __tablename__ = "fattura"
    __table_args__ = (
        tenant_pk("fattura"),
        # FK verso (posizione, cliente) insieme: `cliente_id` è denormalizzato
        # per le query di aging e non può divergere da posizione.cliente_id.
        ForeignKeyConstraint(
            ["tenant_id", "posizione_id", "cliente_id"],
            ["posizione.tenant_id", "posizione.id", "posizione.cliente_id"],
            ondelete="CASCADE",
        ),
        # Il numero fattura è univoco per cliente: protegge dai reimport
        # ripetuti del cassetto fiscale.
        UniqueConstraint(
            "tenant_id", "cliente_id", "numero", name="uq_fattura_tenant_cliente_numero"
        ),
        CheckConstraint("importo >= 0", name="ck_fattura_importo_non_negativo"),
        CheckConstraint("importo_residuo >= 0", name="ck_fattura_residuo_non_negativo"),
        CheckConstraint(
            "importo_residuo <= importo", name="ck_fattura_residuo_entro_importo"
        ),
        CheckConstraint(
            "data_scadenza >= data_emissione", name="ck_fattura_scadenza_dopo_emissione"
        ),
        Index("ix_fattura_tenant_stato", "tenant_id", "stato"),
        Index("ix_fattura_tenant_scadenza", "tenant_id", "data_scadenza"),
    )

    posizione_id: Mapped[str] = mapped_column(ID, index=True)
    cliente_id: Mapped[str] = mapped_column(ID, index=True)
    numero: Mapped[str] = mapped_column(String(64))
    data_emissione: Mapped[date] = mapped_column(Date)
    data_scadenza: Mapped[date] = mapped_column(Date)
    importo: Mapped[Decimal] = mapped_column(IMPORTO)
    importo_residuo: Mapped[Decimal] = mapped_column(IMPORTO)
    stato: Mapped[StatoFattura] = mapped_column(
        enum_col(StatoFattura), default=StatoFattura.GESTIONE
    )
    # Valorizzata su promessa di pagamento: alla scadenza il flusso riprende.
    pausa_fino_a: Mapped[date | None] = mapped_column(Date)

    posizione: Mapped[Posizione] = relationship(back_populates="fatture")
    comunicazioni: Mapped[list["Comunicazione"]] = relationship(
        back_populates="fattura", cascade="all, delete-orphan"
    )
    pagamenti: Mapped[list["Pagamento"]] = relationship(
        back_populates="fattura", cascade="all, delete-orphan"
    )


class Flusso(TenantScoped, Base):
    """Sequenza di step di sollecito assegnata a un mandante (PRD 2.1)."""

    __tablename__ = "flusso"
    __table_args__ = (
        tenant_pk("flusso"),
        tenant_fk("mandante", "mandante_id"),
    )

    mandante_id: Mapped[str] = mapped_column(ID, index=True)
    nome: Mapped[str] = mapped_column(String(128))
    attivo: Mapped[bool] = mapped_column(default=True)

    step: Mapped[list["FlussoStep"]] = relationship(
        back_populates="flusso", cascade="all, delete-orphan", order_by="FlussoStep.ordine"
    )


class FlussoStep(TenantScoped, Base):
    """Singola tappa del flusso: offset in giorni rispetto a T, canale, template."""

    __tablename__ = "flusso_step"
    __table_args__ = (
        tenant_pk("flusso_step"),
        tenant_fk("flusso", "flusso_id"),
        UniqueConstraint("tenant_id", "flusso_id", "ordine", name="uq_step_flusso_ordine"),
    )

    flusso_id: Mapped[str] = mapped_column(ID, index=True)
    ordine: Mapped[int] = mapped_column()
    offset_giorni: Mapped[int] = mapped_column()
    canale: Mapped[Canale] = mapped_column(enum_col(Canale))
    template: Mapped[str] = mapped_column(String(128))

    flusso: Mapped[Flusso] = relationship(back_populates="step")


class Comunicazione(TenantScoped, Base):
    """Istanza di invio (programmata o eseguita) su una fattura."""

    __tablename__ = "comunicazione"
    __table_args__ = (
        tenant_pk("comunicazione"),
        tenant_fk("fattura", "fattura_id"),
        tenant_fk("flusso_step", "step_id", ondelete="SET NULL"),
        # Anti-doppio invio: uno step per fattura esiste una volta sola,
        # anche dopo un ricalcolo dello scheduler (PRD 5.3). Le comunicazioni
        # ad-hoc (step_id NULL) sono per costruzione ripetibili: sono invii
        # forzati dall'operatore, che deve poterne fare più d'uno.
        UniqueConstraint(
            "tenant_id", "fattura_id", "step_id", name="uq_comunicazione_fattura_step"
        ),
        Index("ix_comunicazione_tenant_programmata", "tenant_id", "programmata_per"),
    )

    fattura_id: Mapped[str] = mapped_column(ID, index=True)
    step_id: Mapped[str | None] = mapped_column(ID)
    canale: Mapped[Canale] = mapped_column(enum_col(Canale))
    template: Mapped[str] = mapped_column(String(128))
    programmata_per: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    inviata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stato: Mapped[StatoComunicazione] = mapped_column(
        enum_col(StatoComunicazione), default=StatoComunicazione.PROGRAMMATA
    )
    operatore: Mapped[str | None] = mapped_column(OPERATORE)
    esito_recapito: Mapped[str | None] = mapped_column(String(64))

    fattura: Mapped[Fattura] = relationship(back_populates="comunicazioni")


class Pagamento(TenantScoped, Base):
    """Incasso imputato a una fattura.

    `chiave_idempotenza` identifica lo stesso pagamento a prescindere dalla
    fonte (riconciliazione automatica o registrazione manuale): è ciò che
    impedisce il doppio conteggio segnalato nell'addendum agentico §7.
    """

    __tablename__ = "pagamento"
    __table_args__ = (
        tenant_pk("pagamento"),
        tenant_fk("fattura", "fattura_id"),
        UniqueConstraint(
            "tenant_id", "chiave_idempotenza", name="uq_pagamento_tenant_chiave"
        ),
    )

    fattura_id: Mapped[str] = mapped_column(ID, index=True)
    importo: Mapped[Decimal] = mapped_column(IMPORTO)
    data_pagamento: Mapped[date] = mapped_column(Date)
    origine: Mapped[OriginePagamento] = mapped_column(enum_col(OriginePagamento))
    chiave_idempotenza: Mapped[str] = mapped_column(String(128))
    operatore: Mapped[str | None] = mapped_column(OPERATORE)

    fattura: Mapped[Fattura] = relationship(back_populates="pagamenti")


class AuditLog(TenantScoped, Base):
    """Storico immutabile di transizioni di stato e azioni manuali (PRD 5.4)."""

    __tablename__ = "audit_log"
    __table_args__ = (
        tenant_pk("audit_log"),
        Index("ix_audit_tenant_entita", "tenant_id", "entita", "entita_id"),
    )

    tipo: Mapped[TipoEvento] = mapped_column(enum_col(TipoEvento))
    entita: Mapped[str] = mapped_column(String(32))
    entita_id: Mapped[str] = mapped_column(ID)
    stato_precedente: Mapped[str | None] = mapped_column(String(32))
    stato_successivo: Mapped[str | None] = mapped_column(String(32))
    operatore: Mapped[str | None] = mapped_column(OPERATORE)
    dettaglio: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64))

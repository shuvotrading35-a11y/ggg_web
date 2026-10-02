"""
services/token_vault.py — Encrypted secret storage.
"""

from __future__ import annotations

from database import AsyncSessionLocal, TokenVault
from utils.security import encrypt, decrypt, mask
from sqlalchemy import select


async def vault_add(label: str, value: str) -> bool:
    enc = encrypt(value)
    async with AsyncSessionLocal() as s:
        existing = await s.execute(select(TokenVault).where(TokenVault.label == label))
        obj = existing.scalars().first()
        if obj:
            obj.value_enc = enc
        else:
            s.add(TokenVault(label=label, value_enc=enc))
        await s.commit()
    return True


async def vault_get_decrypted(label: str) -> str | None:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(TokenVault).where(TokenVault.label == label))
        obj = result.scalars().first()
        if not obj:
            return None
        return decrypt(obj.value_enc)


async def vault_list() -> list[dict]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(TokenVault).order_by(TokenVault.label))
        items  = result.scalars().all()
    out = []
    for item in items:
        try:
            val = decrypt(item.value_enc)
            masked = mask(val)
        except Exception:
            masked = "****"
        out.append({"id": item.id, "label": item.label, "masked": masked})
    return out


async def vault_delete(label: str) -> bool:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(TokenVault).where(TokenVault.label == label))
        obj = result.scalars().first()
        if obj:
            await s.delete(obj)
            await s.commit()
            return True
    return False

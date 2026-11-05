"""Encryption for connection secrets. The key comes from SIS_SECRET_KEY (urlsafe base64, 32 bytes; `make init` generates one).

Without a key nothing can be stored. To rotate the key, put the new one in SIS_SECRET_KEY and the old one in SIS_SECRET_KEYS_OLD
(comma-separated): secrets stay readable, and portal-api re-encrypts them with the new key when it starts. Then remove the old key.
SIS_SECRET_KEY_FILE / SIS_SECRET_KEYS_OLD_FILE (files mounted from a vault) work too (sis_contracts.secrets)."""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from sis_contracts.secrets import env_secret


class VaultError(Exception):
    pass


def _fernet() -> MultiFernet:
    key = env_secret("SIS_SECRET_KEY")
    if not key:
        raise VaultError("SIS_SECRET_KEY is not set, so secrets cannot be stored. Run `make init` (or set a key) and restart portal-api.")
    old = [k.strip() for k in env_secret("SIS_SECRET_KEYS_OLD").split(",") if k.strip()]
    try:
        return MultiFernet([Fernet(k.encode()) for k in (key, *old)])  # encrypts with the first, decrypts with any
    except ValueError as e:
        raise VaultError("SIS_SECRET_KEY or SIS_SECRET_KEYS_OLD holds an invalid key: each must be 32 random bytes, urlsafe-base64 encoded.") from e


def available() -> bool:
    try:
        _fernet()
        return True
    except VaultError:
        return False


def encrypt(plain: str) -> bytes:
    return _fernet().encrypt(plain.encode())


def decrypt(blob: bytes) -> str:
    try:
        return _fernet().decrypt(bytes(blob)).decode()
    except InvalidToken as e:
        raise VaultError("the stored secret cannot be decrypted with the current SIS_SECRET_KEY; enter it again") from e


async def reencrypt(pool) -> int:
    """Re-encrypts every stored secret with the current key, so an old key can be retired. A secret no key can read is left alone (the
    Connections page asks for it again)."""
    if not available():
        return 0
    every, current, moved = _fernet(), Fernet(env_secret("SIS_SECRET_KEY").encode()), 0
    async with pool.connection() as conn, conn.transaction():
        for row in await (await conn.execute("select team, provider, secret from connections where secret is not null for update")).fetchall():
            blob = bytes(row["secret"])
            try:
                current.decrypt(blob)
                continue  # already under the current key
            except InvalidToken:
                pass
            try:
                new = every.rotate(blob)
            except InvalidToken:
                continue
            await conn.execute("update connections set secret = %s where team = %s and provider = %s", (new, row["team"], row["provider"]))
            moved += 1
    return moved


def hint(secret: str) -> str:
    return "••••" + secret[-4:] if len(secret) >= 8 else "••••"

"""Conservative filename exclusions; users still review selected content."""
from pathlib import PurePosixPath


def sensitive_path(path):
    name = PurePosixPath(path).name.casefold()
    return (
        name.startswith('.env')
        or name in {
            'madobe.json', 'settings.json', 'credentials.json',
            'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519',
            '.npmrc', '.pypirc', '.keystore', '.netrc', '_netrc',
        }
        or (name.startswith(('service-account', 'service_account')) and name.endswith('.json'))
        or any(word in name for word in ('secret', 'credential', 'api_key', 'apikey'))
        or PurePosixPath(name).suffix in {
            '.db', '.sqlite', '.sqlite3', '.pem', '.key', '.p12', '.pfx', '.keystore', '.jks',
        }
    )

"""Hub manager class."""

from typing import Optional
from warnings import warn

from fastapi import HTTPException
from jose import JWTError
from jose.exceptions import ExpiredSignatureError
from jose.jwt import decode, get_unverified_header
from openbb_core.app.model.abstract.error import OpenBBError
from openbb_core.app.model.credentials import Credentials
from openbb_core.app.model.hub.hub_session import HubSession
from openbb_core.app.model.hub.hub_user_settings import HubUserSettings
from openbb_core.app.model.profile import Profile
from openbb_core.app.model.user_settings import UserSettings
from openbb_core.env import Env
from requests import get, post, put


class HubService:
    """Hub service class."""

    TIMEOUT = 10
    # Mapping of V3 keys to V4 keys for backward compatibility
    V3TOV4 = {
        "API_KEY_ALPHAVANTAGE": "alpha_vantage_api_key",
        "API_BIZTOC_TOKEN": "biztoc_api_key",
        "API_FRED_KEY": "fred_api_key",
        "API_KEY_FINANCIALMODELINGPREP": "fmp_api_key",
        "API_INTRINIO_KEY": "intrinio_api_key",
        "API_POLYGON_KEY": "polygon_api_key",
        "API_KEY_QUANDL": "nasdaq_api_key",
        "API_TRADIER_TOKEN": "tradier_api_key",
    }
    V4TOV3 = {v: k for k, v in V3TOV4.items()}

    def __init__(
        self,
        session: Optional[HubSession] = None,
        base_url: Optional[str] = None,
    ):
        """Initialize Hub service."""
        self._base_url = base_url or Env().HUB_BACKEND
        self._session = session
        self._hub_user_settings: Optional[HubUserSettings] = None

    @property
    def base_url(self) -> str:
        """Get base url."""
        return self._base_url

    @property
    def session(self) -> Optional[HubSession]:
        """Get session."""
        return self._session

    def connect(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        pat: Optional[str] = None,
    ) -> HubSession:
        """Connect to Hub."""
        if email and password:
            self._session = self._get_session_from_email_password(email, password)
            return self._session
        if pat:
            self._session = self._get_session_from_platform_token(pat)
            return self._session
        raise OpenBBError("Please provide 'email' and 'password' or 'pat'")

    def disconnect(self) -> bool:
        """Disconnect from Hub."""
        if self._session:
            result = self._post_logout(self._session)
            self._session = None
            return result
        raise OpenBBError(
            "No session found. Login or provide a 'HubSession' on initialization."
        )

    def push(self, user_settings: UserSettings) -> bool:
        """Push user settings to Hub."""
        if self._session:
            if user_settings.credentials:
                hub_user_settings = self.platform2hub(user_settings.credentials)
                return self._put_user_settings(self._session, hub_user_settings)
            return False
        raise OpenBBError(
            "No session found. Login or provide a 'HubSession' on initialization."
        )

    def pull(self) -> UserSettings:
        """Pull user settings from Hub."""
        if self._session:
            self._hub_user_settings = self._get_user_settings(self._session)
            profile = Profile(hub_session=self._session)
            credentials = self.hub2platform(self._hub_user_settings)
            return UserSettings(profile=profile, credentials=credentials)
        raise OpenBBError(
            "No session found. Login or provide a 'HubSession' on initialization."
        )

    def _get_session_from_email_password(self, email: str, password: str) -> HubSession:
        """Get session from email and password."""
        if not email:
            raise OpenBBError("Email not found.")

        if not password:
            raise OpenBBError("Password not found.")

        response = post(
            url=self._base_url + "/login",
            json={
                "email": email,
                "password": password,
                "remember": True,
            },
            timeout=self.TIMEOUT,
        )

        if response.status_code == 200:
            session = response.json()
            hub_session = HubSession(
                access_token=session.get("access_token"),
                token_type=session.get("token_type"),
                user_uuid=session.get("uuid"),
                email=session.get("email"),
                username=session.get("username"),
                primary_usage=session.get("primary_usage"),
            )
            return hub_session
        status_code = response.status_code
        detail = response.json().get("detail", None)
        raise HTTPException(status_code, detail)

    def _get_session_from_platform_token(self, token: str) -> HubSession:
        """Get session from Platform personal access token."""
        if not token:
            raise OpenBBError("Platform personal access token not found.")

        self.check_token_expiration(token)

        response = post(
            url=self._base_url + "/sdk/login",
            json={
                "token": token,
            },
            timeout=self.TIMEOUT,
        )

        if response.status_code == 200:
            session = response.json()
            hub_session = HubSession(
                access_token=session.get("access_token"),
                token_type=session.get("token_type"),
                user_uuid=session.get("uuid"),
                username=session.get("username"),
                email=session.get("email"),
                primary_usage=session.get("primary_usage"),
            )
            return hub_session
        status_code = response.status_code
        detail = response.json().get("detail", None)
        raise HTTPException(status_code, detail)

    def _post_logout(self, session: HubSession) -> bool:
        """Post logout."""
        access_token = session.access_token.get_secret_value()
        token_type = session.token_type
        authorization = f"{token_type.title()} {access_token}"

        response = get(
            url=self._base_url + "/logout",
            headers={"Authorization": authorization},
            json={"token": access_token},
            timeout=self.TIMEOUT,
        )

        if response.status_code == 200:
            result = response.json()
            return result.get("success", False)
        status_code = response.status_code
        result = response.json()
        detail = result.get("detail", None)
        raise HTTPException(status_code, detail)

    def _get_user_settings(self, session: HubSession) -> HubUserSettings:
        """Get user settings."""
        access_token = session.access_token.get_secret_value()
        token_type = session.token_type
        authorization = f"{token_type.title()} {access_token}"

        response = get(
            url=self._base_url + "/terminal/user",
            headers={"Authorization": authorization},
            timeout=self.TIMEOUT,
        )
        if response.status_code == 200:
            user_settings = response.json()
            filtered = {k: v for k, v in user_settings.items() if v is not None}
            return HubUserSettings.model_validate(filtered)
        status_code = response.status_code
        detail = response.json().get("detail", None)
        raise HTTPException(status_code, detail)

    def _put_user_settings(
        self, session: HubSession, settings: HubUserSettings
    ) -> bool:
        """Put user settings."""
        access_token = session.access_token.get_secret_value()
        token_type = session.token_type
        authorization = f"{token_type.title()} {access_token}"

        response = put(
            url=self._base_url + "/user",
            headers={"Authorization": authorization},
            json=settings.model_dump(),
            timeout=self.TIMEOUT,
        )

        if response.status_code == 200:
            return True
        status_code = response.status_code
        detail = response.json().get("detail", None)
        raise HTTPException(status_code, detail)

    def hub2platform(self, settings: HubUserSettings) -> Credentials:
        """Convert Hub user settings to Platform models."""
        if any(k in settings.features_keys for k in self.V3TOV4):
            deprecated = {
                k: v for k, v in self.V3TOV4.items() if k in settings.features_keys
            }
            msg = ""
            for k, v in deprecated.items():
                msg += f"\n'{k}' -> '{v}', "
            msg = msg.strip(", ")
            warn(
                message=f"\nDeprecated v3 credentials found.\n{msg}"
                "\n\nYou can update them at https://my.openbb.co/app/platform/credentials.",
            )
        # We give priority to v4 keys over v3 keys if both are present
        hub_credentials = {
            self.V3TOV4.get(k, k): settings.features_keys.get(self.V3TOV4.get(k, k), v)
            for k, v in settings.features_keys.items()
        }
        return Credentials(**hub_credentials)

    def platform2hub(self, credentials: Credentials) -> HubUserSettings:
        """Convert Platform models to Hub user settings."""
        # Dump mode json ensures SecretStr values are serialized as strings
        credentials = credentials.model_dump(mode="json", exclude_none=True)
        settings = self._hub_user_settings or HubUserSettings()
        for v4_k, v in sorted(credentials.items()):
            v3_k = self.V4TOV3.get(v4_k, None)
            # If v3 key was there, we keep it
            k = v3_k if v3_k in settings.features_keys else v4_k
            settings.features_keys[k] = v
        return settings

    @staticmethod
    def check_token_expiration(token: str) -> None:
        """Check token expiration, raises exception if expired."""
        try:
            header_data = get_unverified_header(token)
            decode(
                token,
                key="secret",
                algorithms=[header_data["alg"]],
                options={"verify_signature": False, "verify_exp": True},
            )
        except ExpiredSignatureError as e:
            raise OpenBBError("Platform personal access token expired.") from e
        except JWTError as e:
            raise OpenBBError("Failed to decode Platform token.") from e

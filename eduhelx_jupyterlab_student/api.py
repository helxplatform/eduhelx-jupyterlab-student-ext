import jwt
import time
import httpx
from .config import Config
from .git import get_commit_info
from ._version import __version__

class APIException(Exception):
    def __init__(self, response, message):
        super().__init__(message)
        self.response = response

class UnauthorizedException(APIException):
    pass

class ForbiddenException(APIException):
    pass

class Api:
    def __init__(self, config: Config):
        self.config = config
        self._access_token = None
        self.access_token_exp = None
        self._refresh_token = None
        self.refresh_token_exp = None
        self.client = httpx.AsyncClient(
            base_url=f"{ self.api_url }api/v1/",
            headers={
                "User-Agent": f"jls-ext/{ __version__ }"
            }
        )

    @property
    def api_url(self) -> str:
        return self.config.GRADER_API_URL

    @property
    def access_token(self) -> str | None:
        return self._access_token

    @access_token.setter
    def access_token(self, value: str | None):
        self._access_token = value
        self.access_token_exp = jwt.decode(self._access_token, options={"verify_signature": False})["exp"]

    @property
    def refresh_token(self) -> str | None:
        return self._refresh_token

    @refresh_token.setter
    def refresh_token(self, value: str | None):
        self._refresh_token = value
        self.refresh_token_exp = jwt.decode(self._refresh_token, options={"verify_signature": False})["exp"]

    async def _ensure_access_token(self):
        if (
            self.refresh_token is None
            or self.refresh_token_exp is None
            or self.refresh_token_exp - time.time() <= self.config.JWT_REFRESH_LEEWAY_SECONDS
        ):
            await self._login()

        elif (
            self.access_token is None
            or self.access_token_exp is None
            or self.access_token_exp - time.time() <= self.config.JWT_REFRESH_LEEWAY_SECONDS  
        ):
            await self._refresh_access_token()

    async def _handle_response(self, response: httpx.Response):
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 401:
            self.access_token = None
            raise UnauthorizedException(response, "You aren't logged in")
        elif response.status_code == 403:
            raise ForbiddenException(response, "You lack the permission to make this API request")
        else:
            raise APIException(response, f"API request to { response.request.url } failed with status code { response.status_code } { response.text }")

    async def _make_request(self, method: str, endpoint: str, verify_credentials=True, headers={}, **kwargs):
        if verify_credentials: await self._ensure_access_token()
        res = await self.client.request(
            method,
            endpoint,
            headers={
                **({"Authorization": f"Bearer { self.access_token }"} if self.access_token is not None else {}),
                **headers
            },
            **kwargs
        )
        return await self._handle_response(res)

    async def _get(self, endpoint: str, **kwargs):
        return await self._make_request("GET", endpoint, **kwargs)

    async def _post(self, endpoint: str, **kwargs):
        return await self._make_request("POST", endpoint, **kwargs)

    async def _refresh_access_token(self):
        try:
            self.access_token = await self._post("refresh", verify_credentials=False, json={
                "refresh_token": self.refresh_token
            })
        except:
            self.access_token = None
            self.refresh_token = None
            

    async def _login(self):
        res = await self._post("login", verify_credentials=False, json={
            "onyen": self.config.USER_ONYEN,
            "autogen_password": self.config.USER_AUTOGEN_PASSWORD
        })
        self.access_token = res.get("access_token")
        self.refresh_token = res.get("refresh_token")

    async def get_assignments(self):
        return await self._get("assignments/self")

    async def get_course(self):
        return await self._get("course")

    async def get_student(self):
        return await self._get("student/self")

    async def get_assignment_submissions(self, assignment_id: int, git_path="./"):
        submissions = await self._get("submissions/self", params={
            "assignment_id": assignment_id
        })
        for submission in submissions:
            submission["commit"] = get_commit_info(submission["commit_id"], path=git_path)
        
        return submissions

    async def post_submission(self, assignment_id: str, commit_id: str):
        return await self._post("submission", json={
            "assignment_id": assignment_id,
            "commit_id": commit_id
        })
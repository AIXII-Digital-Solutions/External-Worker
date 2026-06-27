from azure.identity.aio import ClientSecretCredential
from msgraph import GraphServiceClient

from settings import MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET, MS_GRAPHSCOPES


class MSGraphClient:
    def __init__(self):
        self.credential = ClientSecretCredential(
            tenant_id=MS_TENANT_ID,
            client_id=MS_CLIENT_ID,
            client_secret=MS_CLIENT_SECRET,
        )

        self.client = GraphServiceClient(self.credential, MS_GRAPHSCOPES)
        self._get_client()

    def _get_client(self) -> GraphServiceClient:
        """Returns GraphServiceClient"""
        return self.client




__all__ = [
    "MSGraphClient",
]



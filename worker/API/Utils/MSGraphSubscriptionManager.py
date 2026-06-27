from datetime import datetime, timedelta, timezone

from msgraph import GraphServiceClient
from msgraph.generated.models.subscription import Subscription
from Utils import DBProxy

from Config import setup_logger
from settings import MS_WEBHOOK_URL, MS_WEBHOOK_SECRET, MS_WEBHOOK_LIFECYCLE_URL
from API.Exceptions import InvalidSubscriptionError
from API.Clients import MSGraphClient

logger = setup_logger(name="MSGraph_sub_manager")


async def create_or_update_subscription(
        db_proxy: DBProxy,
        graph_client: GraphServiceClient | MSGraphClient = MSGraphClient(),
        change_type: str | None = None,
        resource: str | None = None,
        subscription_id: str | None = None,
        ttl: int = 60*65
    ):
    if change_type is None or resource is None:
        raise InvalidSubscriptionError()

    if graph_client == GraphServiceClient:
        client = graph_client
    else:
        client = graph_client.client

    redis_key = f"subscription_{resource}"
    cached_sub_id = await db_proxy.redis_get(redis_key)

    if cached_sub_id:
        _subscription_id = cached_sub_id
    else:
        _subscription_id = None

    if subscription_id:
        _subscription_id = subscription_id

    expiration_time = datetime.now(timezone.utc) + timedelta(days=3)

    if _subscription_id:
        try:
            subscription = Subscription(expiration_date_time=expiration_time)
            await client.subscriptions.by_subscription_id(_subscription_id).patch(subscription)
            logger.info(f"Subscription {_subscription_id} renewed until {expiration_time}")
        except Exception as e:
            logger.info(f"Failed to renew subscription {_subscription_id}: {e}")
            _subscription_id = None

    if not _subscription_id:
        subscription = Subscription(
            change_type=change_type,
            notification_url=MS_WEBHOOK_URL,
            resource=resource,
            expiration_date_time=expiration_time,
            client_state=MS_WEBHOOK_SECRET,
            lifecycle_notification_url=MS_WEBHOOK_LIFECYCLE_URL
        )
        created_sub = await client.subscriptions.post(subscription)
        _subscription_id = created_sub.id
        logger.info(f"Created new subscription: {_subscription_id}")

    await db_proxy.redis_set(redis_key, _subscription_id, ttl=ttl)
    logger.debug(f"Redis SET {redis_key} -> {_subscription_id}")

    return _subscription_id


__all__ = ["create_or_update_subscription"]

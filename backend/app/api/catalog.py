"""Meta endpoints: health check and the device / backup catalogs."""

from fastapi import APIRouter

from app import __version__
from app.api.deps import ContextDep
from app.catalog import BACKUP_CATALOG, DEVICE_CATALOG
from app.schemas import (
    BackupCatalogResponse,
    BackupTemplate,
    DeviceCatalogResponse,
    DeviceTemplate,
    HealthResponse,
)

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health(context: ContextDep) -> HealthResponse:
    """Whether the service is up, and whether AI device identification is available."""
    return HealthResponse(ok=True, version=__version__, ai_enabled=context.identifier.ai_enabled)


@router.get("/catalog/devices")
async def device_catalog() -> DeviceCatalogResponse:
    """Typical figures for every kind of medical device."""
    return DeviceCatalogResponse(
        devices=[DeviceTemplate.model_validate(spec) for spec in DEVICE_CATALOG.values()]
    )


@router.get("/catalog/backups")
async def backup_catalog() -> BackupCatalogResponse:
    """Typical figures for every kind of backup power."""
    return BackupCatalogResponse(
        backups=[BackupTemplate.model_validate(spec) for spec in BACKUP_CATALOG.values()]
    )

"""A household's medical devices and backup power sources."""

from fastapi import APIRouter, status

from app.api.deps import HouseholdServiceDep, OwnerAccess
from app.schemas import (
    BackupCreate,
    BackupResponse,
    BackupSource,
    BackupUpdate,
    Device,
    DeviceCreate,
    DeviceResponse,
    DeviceUpdate,
)

router = APIRouter(tags=["devices and backups"])


@router.post("/households/{household_id}/devices", status_code=status.HTTP_201_CREATED)
async def add_device(
    body: DeviceCreate, access: OwnerAccess, service: HouseholdServiceDep
) -> DeviceResponse:
    """Add a device. Fields left out are filled in from the catalog entry for its kind."""
    device = await service.add_device(access.household, body)
    return DeviceResponse(device=Device.model_validate(device))


@router.patch("/households/{household_id}/devices/{device_id}")
async def update_device(
    device_id: str, body: DeviceUpdate, access: OwnerAccess, service: HouseholdServiceDep
) -> DeviceResponse:
    """Change any device field except `id` and `kind`."""
    device = await service.update_device(access.household, device_id, body)
    return DeviceResponse(device=Device.model_validate(device))


@router.delete(
    "/households/{household_id}/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_device(device_id: str, access: OwnerAccess, service: HouseholdServiceDep) -> None:
    """Remove a device."""
    await service.remove_device(access.household, device_id)


@router.post("/households/{household_id}/backups", status_code=status.HTTP_201_CREATED)
async def add_backup(
    body: BackupCreate, access: OwnerAccess, service: HouseholdServiceDep
) -> BackupResponse:
    """Add a backup power source. Fields left out come from the catalog entry for its type."""
    backup = await service.add_backup(access.household, body)
    return BackupResponse(backup=BackupSource.model_validate(backup))


@router.patch("/households/{household_id}/backups/{backup_id}")
async def update_backup(
    backup_id: str, body: BackupUpdate, access: OwnerAccess, service: HouseholdServiceDep
) -> BackupResponse:
    """Change any backup field except `id`."""
    backup = await service.update_backup(access.household, backup_id, body)
    return BackupResponse(backup=BackupSource.model_validate(backup))


@router.delete(
    "/households/{household_id}/backups/{backup_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_backup(backup_id: str, access: OwnerAccess, service: HouseholdServiceDep) -> None:
    """Remove a backup power source."""
    await service.remove_backup(access.household, backup_id)

"""Import all ORM models so `Base.metadata` is fully populated.

This module must be imported before using `Base.metadata` for Alembic
autogenerate or `create_all`, so that every model class has been
registered with the shared declarative base.
"""
from app.models.base import Base
from app.models.server import Server
from app.models.sql_instance import SqlInstance
from app.models.disk import Disk
from app.models.backup_job import BackupJob
from app.models.job_run import JobRun
from app.models.backup_record import BackupRecord
from app.models.restore_operation import RestoreOperation
from app.models.verification_run import VerificationRun
from app.models.alert import Alert
from app.models.user import User
from app.models.agent_credential_access_log import AgentCredentialAccessLog

__all__ = [
    "Base",
    "Server",
    "SqlInstance",
    "Disk",
    "BackupJob",
    "JobRun",
    "BackupRecord",
    "RestoreOperation",
    "VerificationRun",
    "Alert",
    "User",
    "AgentCredentialAccessLog",
]

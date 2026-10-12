from __future__ import annotations

import secrets
from datetime import UTC, datetime

from rhetorica_backend.infra.postgres.chat_models import ChatLlmCredentialBindingOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError


class LlmCredentialsUnavailableError(RuntimeError):
    """The selected LLM connector has no usable credentials."""


class LlmCredentialBindingRepository:
    """Persists conversation-scoped credential positions without secret values."""

    def __init__(self, db: DatabaseRuntime):
        self._db = db

    def resolve(
        self,
        *,
        conversation_id: str,
        llm_connector_id: str,
        credential_set_version: int,
        credential_count: int,
    ) -> int:
        if credential_count <= 0:
            raise LlmCredentialsUnavailableError("llm_credentials_missing")

        while True:
            now = datetime.now(tz=UTC)
            with self._db.session() as session:
                statement = (
                    select(ChatLlmCredentialBindingOrm)
                    .where(
                        ChatLlmCredentialBindingOrm.conversation_id == conversation_id,
                        ChatLlmCredentialBindingOrm.llm_connector_id == llm_connector_id,
                    )
                    .with_for_update()
                )
                binding = session.execute(statement).scalar_one_or_none()
                if binding is not None and self._is_valid(
                    binding,
                    credential_set_version=credential_set_version,
                    credential_count=credential_count,
                ):
                    return binding.credential_index

                selected_index = secrets.randbelow(credential_count)
                if binding is None:
                    session.add(
                        ChatLlmCredentialBindingOrm(
                            conversation_id=conversation_id,
                            llm_connector_id=llm_connector_id,
                            credential_index=selected_index,
                            credential_set_version=credential_set_version,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    try:
                        session.commit()
                    except IntegrityError:
                        session.rollback()
                        existing = self._get(session, conversation_id, llm_connector_id)
                        if existing is not None and self._is_valid(
                            existing,
                            credential_set_version=credential_set_version,
                            credential_count=credential_count,
                        ):
                            return existing.credential_index
                        continue
                    return selected_index

                binding.credential_index = selected_index
                binding.credential_set_version = credential_set_version
                binding.updated_at = now
                session.commit()
                return selected_index

    def rebind(
        self,
        *,
        conversation_id: str,
        llm_connector_id: str,
        expected_index: int,
        credential_set_version: int,
        new_index: int,
    ) -> bool:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            statement = (
                update(ChatLlmCredentialBindingOrm)
                .where(
                    ChatLlmCredentialBindingOrm.conversation_id == conversation_id,
                    ChatLlmCredentialBindingOrm.llm_connector_id == llm_connector_id,
                    ChatLlmCredentialBindingOrm.credential_index == expected_index,
                    ChatLlmCredentialBindingOrm.credential_set_version
                    == credential_set_version,
                )
                .values(
                    credential_index=new_index,
                    updated_at=now,
                )
            )
            result = session.execute(statement)
            session.commit()
            return result.rowcount == 1

    @staticmethod
    def _get(session, conversation_id: str, llm_connector_id: str):
        return session.execute(
            select(ChatLlmCredentialBindingOrm).where(
                ChatLlmCredentialBindingOrm.conversation_id == conversation_id,
                ChatLlmCredentialBindingOrm.llm_connector_id == llm_connector_id,
            )
        ).scalar_one_or_none()

    @staticmethod
    def _is_valid(
        binding: ChatLlmCredentialBindingOrm,
        *,
        credential_set_version: int,
        credential_count: int,
    ) -> bool:
        return (
            binding.credential_set_version == credential_set_version
            and 0 <= binding.credential_index < credential_count
        )

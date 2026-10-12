"""Record team membership on coding tasks and backfill default coding teams.

Every workspace preset that still selects a coordinator, developer and QA
receives one equivalent active team so existing workspaces keep their behavior.
The legacy developer/QA IDs remain in the preset as a compatibility projection.

Revision ID: 6cd7e8f9a0b1
Revises: 5bc6d7e8f9a0
"""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision = "6cd7e8f9a0b1"
down_revision = "5bc6d7e8f9a0"
branch_labels = None
depends_on = None

DEFAULT_ASSISTANT_POLICY_ID = "__default_assistant__"
DEFAULT_TEAM_NAME = "Default coding team"

_defaults = sa.table(
    "workspace_coding_defaults",
    sa.column("workspace_id", sa.String),
    sa.column("resources_json", sa.JSON),
    sa.column("updated_by", sa.String),
)
_teams = sa.table(
    "coding_agent_team",
    sa.column("id", sa.String),
    sa.column("workspace_id", sa.String),
    sa.column("name", sa.String),
    sa.column("active_revision_id", sa.String),
    sa.column("latest_revision", sa.Integer),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
_revisions = sa.table(
    "coding_agent_team_revision",
    sa.column("id", sa.String),
    sa.column("workspace_id", sa.String),
    sa.column("team_id", sa.String),
    sa.column("revision", sa.Integer),
    sa.column("coordinator_agent_profile_id", sa.String),
    sa.column("coordinator_instruction", sa.String),
    sa.column("members_json", sa.JSON),
    sa.column("is_active", sa.Boolean),
    sa.column("created_by", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def upgrade():
    with op.batch_alter_table("coding_agent_task") as batch:
        batch.add_column(sa.Column("team_member_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("team_member_label", sa.String(120), nullable=True))
    connection = op.get_bind()
    now = datetime.now(UTC)
    for row in connection.execute(sa.select(_defaults)).mappings().all():
        resources = dict(row["resources_json"] or {})
        developer = resources.get("developer_agent_id")
        qa = resources.get("qa_agent_id")
        coordinator = resources.get("root_agent_id") or DEFAULT_ASSISTANT_POLICY_ID
        if resources.get("coding_team_id") or not developer or not qa:
            continue
        if len({developer, qa, coordinator}) != 3:
            # An invalid legacy preset stays visibly unavailable instead of being repaired.
            continue
        workspace_id = row["workspace_id"]
        team_id = str(uuid5(NAMESPACE_URL, f"rhetorica/coding-team/{workspace_id}/default"))
        revision_id = str(uuid5(NAMESPACE_URL, f"rhetorica/coding-team/{team_id}/1"))
        existing_names = set(
            connection.execute(
                sa.select(_teams.c.name).where(_teams.c.workspace_id == workspace_id)
            ).scalars()
        )
        if connection.execute(sa.select(_teams.c.id).where(_teams.c.id == team_id)).first() is None:
            name = DEFAULT_TEAM_NAME
            suffix = 2
            while name in existing_names:
                name = f"{DEFAULT_TEAM_NAME} {suffix}"
                suffix += 1
            connection.execute(
                _teams.insert().values(
                    id=team_id,
                    workspace_id=workspace_id,
                    name=name,
                    active_revision_id=revision_id,
                    latest_revision=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                _revisions.insert().values(
                    id=revision_id,
                    workspace_id=workspace_id,
                    team_id=team_id,
                    revision=1,
                    coordinator_agent_profile_id=coordinator,
                    coordinator_instruction="",
                    members_json=[
                        {
                            "id": "developer",
                            "agent_profile_id": developer,
                            "label": "Developer",
                            "instruction": "",
                            "capability": "candidate",
                            "skill_version_bindings": [],
                        },
                        {
                            "id": "qa",
                            "agent_profile_id": qa,
                            "label": "QA",
                            "instruction": "",
                            "capability": "review",
                            "skill_version_bindings": [],
                        },
                    ],
                    is_active=True,
                    created_by=None,
                    created_at=now,
                )
            )
        resources["coding_team_id"] = team_id
        connection.execute(
            _defaults.update()
            .where(_defaults.c.workspace_id == workspace_id)
            .values(resources_json=resources)
        )


def downgrade():
    connection = op.get_bind()
    for row in connection.execute(sa.select(_defaults)).mappings().all():
        resources = dict(row["resources_json"] or {})
        if "coding_team_id" not in resources:
            continue
        resources.pop("coding_team_id")
        # A team-only preset has no legacy equivalent; clearing it keeps Coding
        # visibly unconfigured instead of resurrecting an invalid selection.
        legacy = (
            resources
            if resources.get("developer_agent_id") and resources.get("qa_agent_id")
            else None
        )
        connection.execute(
            _defaults.update()
            .where(_defaults.c.workspace_id == row["workspace_id"])
            .values(resources_json=legacy)
        )
    with op.batch_alter_table("coding_agent_task") as batch:
        batch.drop_column("team_member_label")
        batch.drop_column("team_member_id")

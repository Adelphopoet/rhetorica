from unittest.mock import patch

from rhetorica_backend.infra.mattermost import MattermostClient


def test_create_reaction_posts_mattermost_reaction_payload() -> None:
    client = MattermostClient(
        base_url="https://tag.example.test",
        token="secret",
    )

    with patch.object(client, "_request_json", return_value={}) as request_json:
        client.create_reaction(
            user_id="bot-1",
            post_id="post-1",
            emoji_name="eyes",
        )

    request_json.assert_called_once_with(
        "POST",
        "/api/v4/reactions",
        json={
            "user_id": "bot-1",
            "post_id": "post-1",
            "emoji_name": "eyes",
        },
    )

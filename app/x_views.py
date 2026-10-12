"""Ephemeral X speech previews. No archive, cursor or delivery writes."""
from pydantic import BaseModel, ConfigDict, field_validator
from .settings_store import XAccount
from .x_news import XClient, parse_posts


class XPreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str

    @field_validator('username')
    @classmethod
    def checked_username(cls, value):
        return XAccount(username=value).username


async def preview_posts(client: XClient, username: str) -> dict:
    user = await client.resolve_user(username)
    posts = parse_posts(await client.fetch_posts(user['id'], {}, max_results=10), user)
    return {'state':'ok','user':user,'items':[
        {'source_type':'x','title':'X 用户言论 · @'+post['username'],
         'summary':post['raw_text'][:160],'raw_text':post['raw_text'],
         'published_at':post['created_at'],'url':f"https://x.com/{post['username']}/status/{post['post_id']}",
         'related_symbols':[], 'x_metadata':post}
        for post in posts[:10]]}

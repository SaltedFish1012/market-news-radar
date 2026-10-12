"""Speech policy. Author attribution is separate from truth of the claim."""
from .signals import matched_keywords


def x_should_push(post, account, watchlist, priority):
    if account.get('push_mode','filtered') == 'all':
        return True
    keywords = account.get('keywords') or watchlist.get('theme_keywords',[])
    return bool(matched_keywords(post.get('raw_text',''),keywords)) or priority in ('high','critical')


def x_alert(event, account, related):
    meta = event['x_metadata']
    refs = '\n'.join(f"{r.get('type','引用')}对象：@{r.get('username') or '未知作者'}（ID {r.get('author_id') or '未知'}），帖子 {r['post_id']}" for r in meta.get('references',[])[:3])
    return {'key':event['event_key']+':fast','title':event['title'], 'severity':event['priority'],
        'body':f"X 用户言论\n作者：{meta.get('author_name','')} @{meta['username']}（ID {meta['author_id']}）\n"
               f"发布时间：{event['published_at']}\n正文摘录：{event['raw_text'][:400]}\n{refs}\n"
               f"关联标的（规则／绑定，影响待确认）：{', '.join(related[:8]) or '未识别'}\n"
               f"原文：{event['url']}\n这是用户言论，内容尚未交叉核验；完整原文已归档。"}

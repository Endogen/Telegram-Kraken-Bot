from functools import wraps
from utils import get_chat_id


def restrict_access(allowed_user_ids):
    def _restrict_access(chat_fn):
        @wraps(chat_fn)
        def _wrapped_fn(bot, updater):
            chat_id = get_chat_id(updater)
            if str(chat_id) in allowed_user_ids:
                return chat_fn(bot, updater, chat_id)
            else:
                bot.send_message(chat_id, text="Access denied")
                return

        return _wrapped_fn
    return _restrict_access

import re


STOPWORDS = frozenset({
    '的', '了', '在', '是', '和', '与', '及', '也', '都', '而', '但', '却',
    '这', '那', '有', '为', '被', '把', '将', '就', '还', '又', '并', '且',
    '其', '之', '于', '对', '从', '向', '以', '所', '个', '等', '中', '上',
    '下', '里', '后', '前', '时', '称', '说', '据', '报道', '表示', '目前',
    '已经', '正在', '随后', '此前', '其中', '以及', '一个', '没有', '不是',
})

RELATIVE_TIME = ('上一条', '此前', '当天', '当日', '当晚', '次日', '翌日',
                 '前一天', '昨日', '昨天', '今天', '今日', '本月', '上周',
                 '稍早', '随后', '最近')


def normalize_text(value):
    if not isinstance(value, str):
        raise TypeError('文本必须是字符串')
    return re.sub(r'\s+', ' ', value).strip()


def tokens(value):
    return re.findall(r'[A-Za-z0-9]+|[㐀-鿿]', value.casefold())


def _is_cjk(char):
    return '㐀' <= char <= '鿿'


def keywords(value):
    """供候选定位使用的词项：中文二元组 + 英文数字词，去掉常见虚词。"""
    normalized = normalize_text(value).casefold()
    result = []
    chars = [char for char in normalized if _is_cjk(char)]
    for index in range(len(chars) - 1):
        bigram = chars[index] + chars[index + 1]
        if bigram not in STOPWORDS:
            result.append(bigram)
    for word in re.findall(r'[a-z0-9]+', normalized):
        if len(word) > 1 and word not in STOPWORDS:
            result.append(word)
    return result


def relative_time_terms(value):
    return [term for term in RELATIVE_TIME if term in value]


def names_in(value, candidates):
    return [name for name in candidates if name and name in value]

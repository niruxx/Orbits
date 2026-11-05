"""Normalize OCS detail/comment payloads. Remote markup is displayed as plain text."""
from html.parser import HTMLParser


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("br", "p", "div", "li", "h1", "h2", "h3") and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        if tag in ("p", "div", "li", "h1", "h2", "h3") and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value, limit=16000):
    parser = PlainText()
    parser.feed(str(value or "")[:100000])
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)[:limit]


def payload(response):
    if not isinstance(response, dict):
        raise ValueError("Invalid OCS response")
    response = response.get("ocs", response)
    meta = response.get("meta") or {}
    status = response.get("status") or meta.get("status")
    code = response.get("statuscode") or meta.get("statuscode")
    if status not in ("ok", None) or code is not None and str(code) not in ("100", "200"):
        raise RuntimeError(plain_text(response.get("message") or meta.get("message") or "Store request failed", 300))
    return response.get("data")


def details(response):
    data = payload(response)
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, dict) and "content" in data:
        data = data["content"]
    if not isinstance(data, dict):
        raise ValueError("Store item not found")
    try:
        score = float(data["score"])
        if not 0 <= score <= 100:
            score = None
    except (KeyError, TypeError, ValueError):
        score = None
    return {"score":score, "version":plain_text(data.get("version"),100),
            "changed":plain_text(data.get("changed"),100),
            "changelog":plain_text(data.get("changelog") or data.get("change_log")),
            "description":plain_text(data.get("description") or data.get("summary")),
            "comment_count":data.get("comments")}


def comments(response, page_size=10):
    data = payload(response)
    if isinstance(data, dict):
        data = data.get("comment", [])
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        data = []
    result = []
    def visit(records, depth=0):
        if depth > 6:
            return
        if isinstance(records, dict):
            records = [records]
        for record in records[:100]:
            if not isinstance(record, dict) or len(result) >= 100:
                continue
            result.append({"id":str(record.get("id", "")), "author":plain_text(record.get("user") or record.get("username") or "Anonymous",100),
                           "subject":plain_text(record.get("subject"),200), "text":plain_text(record.get("text") or record.get("message"),4000),
                           "date":plain_text(record.get("date"),100), "depth":depth})
            children = record.get("children") or {}
            if isinstance(children, dict):
                visit(children.get("comment") or [], depth+1)
    visit(data)
    return result, len(data) >= page_size

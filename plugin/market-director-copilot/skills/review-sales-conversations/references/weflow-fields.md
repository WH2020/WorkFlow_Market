# WeFlow 规范化字段

规范化消息使用以下字段：`session_id`、`salesperson_id`、`message_id`、`timestamp`、`sender`、`sent_by_me`、`text`、`quote`、`media`、`source_file`。

`source_file` 优先使用 WeFlow 消息提供的本地来源文件；API 未提供本地文件时，`weflow_sync.py` 写入对应的 `weflow://` 消息接口 URI，保证每条消息仍有可追溯来源。不要把 Token 或完整授权请求 URL 写入该字段。

`media` 可包含：`type`、`local_path`、`sha256`、`transcript_path`、`frame_paths`、`status`。

转写文件保留模型、语言、分段时间戳和内容。引用证据时优先引用消息时间戳与 `message_id`，媒体内容同时给出文件路径。


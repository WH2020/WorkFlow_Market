# WeFlow 规范化字段

规范化消息使用以下字段：`session_id`、`salesperson_id`、`message_id`、`timestamp`、`sender`、`sent_by_me`、`text`、`quote`、`media`、`source_file`。

`media` 可包含：`type`、`local_path`、`sha256`、`transcript_path`、`frame_paths`、`status`。

转写文件保留模型、语言、分段时间戳和内容。引用证据时优先引用消息时间戳与 `message_id`，媒体内容同时给出文件路径。


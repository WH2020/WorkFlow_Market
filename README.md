# 市场总监工作台

面向市场总监的本地化工作流项目，配合 ChatGPT 桌面版个人账号使用。项目覆盖前沿行业资料研究、地方政府合作方案、销售微信复盘、客户与销售管线管理、资源调动以及 CEO 周报 PPT。

## 能做什么

- **行业研究**：整理脑机、具身智能、数采及相邻领域资料，登记来源、发布日期、访问日期、适用地域和可信度。
- **政府合作**：根据知识库和具体地区场景起草合作框架，区分已证实事实、分析判断和待确认事项。
- **销售复盘**：读取 WeFlow 同步的文字、图片、语音和视频，提取客户进展、异议、承诺、风险、资源申请和下一步动作。
- **管线管理**：维护客户、活动、销售人员和资源申请台账，形成客户分析和销售复盘输入。
- **演示文稿**：使用可编辑模板生成政府方案、客户方案、行业研究、销售复盘和 CEO 周报 PPT。
- **定时周报**：每周五 18:00（Asia/Shanghai）汇总，默认面向 CEO、7-10 页、默认 8 页，不自动向第三方发送。

## 快速开始

1. 用 ChatGPT 桌面版打开本项目目录：`D:\PersonalWorkSpace\WorkFlow4Market`。
2. 安装个人插件 `market-director-copilot`，详见 [docs/使用说明.md](docs/使用说明.md)。
3. 安装 Python 依赖并使用项目虚拟环境：

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

4. 如需微信复盘，启动 WeFlow 的 HTTP API，在 `设置` → `API 服务` 生成并启用 Access Token，然后只在本机用户环境变量中保存：

   ```powershell
   [Environment]::SetEnvironmentVariable("WEFLOW_ACCESS_TOKEN", "你的Token", "User")
   ```

5. 在 `data/sales/salespeople.json` 填入已获授权的会话 ID，并将对应销售的 `active` 改为 `true`。

## 项目结构

```text
config/                         全局配置
data/knowledge/                 研究来源登记
data/sales/                     客户、活动、销售和资源台账
data/weekly/                    周报结构化输入示例
data/weflow/                    WeFlow 运行时数据（原始媒体和转写不入 Git）
library/templates/              可编辑 PPT 模板库
plugin/market-director-copilot/ ChatGPT 个人插件源码、Skill、脚本和插件资产
outputs/                        方案、复盘和演示文稿输出
docs/                           使用说明和操作约定
```

## 安全边界

- Token 不写入仓库，不提交到 Git，不发送到聊天中。
- WeFlow 媒体下载仅接受本机 `127.0.0.1`/`localhost` 地址。
- 原始微信记录可以处理，但不会自动发送给第三方；对外发送文件前必须人工确认。
- 对外方案不得虚构公司能力、案例、投资额、收入、就业或政府承诺。

完整操作步骤、命令和排障方法见 [docs/使用说明.md](docs/使用说明.md)。

# news-pusher

每 5 分钟抓取国外权威媒体 RSS 头条，出现新头条时推送到钉钉群。纯 Python 标准库实现，零第三方依赖，支持 GitHub Actions（推荐，24/7 免运维）和本机 launchd（备份）两种运行方式。

## 工作原理

```
GitHub Actions (每5分钟)  →  news_pusher.py  →  抓取各媒体 RSS
        ↑                                        ↓ 与 state.json 去重
        └── GITHUB_TOKEN 提交 state.json  ←  钉钉机器人(加签) 推送新增头条
```

- 只推**新增**头条，无新头条不推送、不产生 commit
- 推送成功才把条目记为已推送，失败下轮自动重试
- 单源失败不影响其他源

## 前置条件（只需做一次，5 分钟）

### 1. 创建钉钉机器人

1. 打开目标钉钉群 → 右上角 **群设置** → **智能群助手** → **添加机器人** → **自定义机器人**
2. 安全设置选择 **加签**（Signature）
3. 记下两个值：
   - **Webhook 地址**（形如 `https://oapi.dingtalk.com/robot/send?access_token=xxx`）
   - **加签密钥**（`SEC` 开头的一串字符）

> 注意：不要把这两个值写进任何会提交到 GitHub 的文件。

### 2. 部署到 GitHub Actions（推荐）

1. 在 github.com 新建一个 **公开（Public）** 仓库（私有仓库免费额度不够 5 分钟一次的频率）
2. 把本项目推上去：
   ```bash
   cd /Users/tuhui/Downloads/rea/news-pusher
   git init && git add . && git commit -m "init news pusher"
   git remote add origin git@github.com:<你的用户名>/<仓库名>.git
   git push -u origin main
   ```
3. 仓库页面 → **Settings → Secrets and variables → Actions → New repository secret**，添加两个 secret：
   - `DD_WEBHOOK` = 你的 webhook 地址
   - `DD_SECRET` = 你的加签密钥
4. **Actions** 标签页 → 左侧选中 `news-pusher` 工作流 → **Run workflow**（手动触发一次测试）→ 钉钉群应收到头条消息
5. 之后无需任何维护。定时任务会每 5 分钟自动运行

### 3. 本机备份模式（可选）

Mac 保持开机不睡眠时可替代/补充 Actions（注意：本机网络需能直连这些媒体）：

```bash
# 重要：先复制到 LaunchAgents，再编辑「副本」填入真实值。
# 不要在仓库目录里编辑带真实密钥的 plist，避免误提交到公开仓库。
cp com.tuhui.news-pusher.plist ~/Library/LaunchAgents/
vi ~/Library/LaunchAgents/com.tuhui.news-pusher.plist   # 替换 REPLACE_WITH_YOUR_WEBHOOK / REPLACE_WITH_YOUR_SECRET
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tuhui.news-pusher.plist
# 卸载：launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tuhui.news-pusher.plist
```

手动跑一次测试：

```bash
DD_WEBHOOK=你的webhook DD_SECRET=你的密钥 python3 news_pusher.py
DD_WEBHOOK=x DD_SECRET=y python3 news_pusher.py --dry-run   # 只打印不推送
```

## 配置说明（config.json）

| 字段 | 说明 |
|---|---|
| `feeds` | 订阅源列表，`name` 显示名 + `url` 地址（支持 RSS 2.0 和 Atom），可自由增删。特殊源需加 `type`：`"type": "sina_roll"` 表示新浪财经滚动 JSON 接口（`params` 为接口参数，见 config.json 示例） |
| `max_new_per_run` | 单次最多推送条数（防刷屏，超出部分下轮继续推） |
| `max_seen_per_feed` | 每个源保留的已推送记录上限（自动裁剪，防 state.json 膨胀） |
| `request_timeout` | 抓取超时（秒） |

## 常见问题

- **时间不对？** GitHub Actions 的 runner 是 UTC 时区，消息头时间是 UTC。本机模式显示本地时间。
- **定时延迟？** GitHub 官方说明高负载时定时任务可能延迟几分钟，属正常现象。
- **想改频率？** 改 `.github/workflows/news-pusher.yml` 里的 cron 表达式（`*/5 * * * *` 是 GitHub 支持的最短间隔）。
- **推送重复了？** 检查钉钉机器人安全设置是否为「加签」，以及 secrets 是否填对。

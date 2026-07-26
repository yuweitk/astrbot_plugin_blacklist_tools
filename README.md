# AstrBot 黑名单工具插件 (Rewrite)

> 基于 [ctrlkk/astrbot_plugin_blacklist_tools](https://github.com/ctrlkk/astrbot_plugin_blacklist_tools) 重构增强版。
>
> 感谢原作者 **ctrlkk** 的优秀基础实现，本版本在其基础上进行了功能扩展和架构优化。

## 与原版的区别

| 功能点 | 原版 (ctrlkk) | 本版 (Rewrite) |
|--------|--------------|----------------|
| **入站拦截** | 阻止黑名单用户的消息 | ✅ 同样支持 |
| **出站拦截** | ❌ 不支持 | ✅ **新增** Monkey-patch `Context.send_message`，阻止向黑名单用户主动发消息（私聊/群聊@） |
| **用户ID 提取** | 仅支持直接输入 ID | ✅ **三层提取**：At 组件 → `<@OPENID>` 正则 → `raw_message.mentions`，适配 QQ 官方 API |
| **昵称自动获取** | ❌ 不支持 | ✅ **自动解析**用户昵称，展示更友好 |
| **可配置拉黑提示** | ❌ 固定提示 | ✅ **自定义消息**，支持配置开关 |
| **过期记录自动清理** | ❌ 永远保留 | ✅ **可配自动删除**过期记录（默认 1 天后清理） |
| **管理员是否可被拉黑** | ❌ 不可配置 | ✅ **可配置** `allow_blacklist_admin` |
| **拉黑状态展示开关** | ❌ 总是展示 | ✅ **可配置** `show_blacklist_status` |
| **CJK 对齐** | 等宽截断 | ✅ **自适应列宽**（CJK 字符按 2 宽度计算） |
| **命令前缀** | `/black` / `/bl` | ✅ 同上，新增 `/blacklist` 别名 |

## 功能特性

- 🚫 **双向拦截**：阻止黑名单用户的消息（入站），也阻止向黑名单用户发送消息（出站）
- ⏰ **临时/永久黑名单**：支持设置时长或永久拉黑
- 🛠️ **管理员命令**：完整的黑名单管理命令集
- 🤖 **LLM 工具**：允许 LLM 直接调用 `block_user` 工具拉黑用户
- 📊 **分页显示**：支持分页查看黑名单列表，自动适配 CJK 字符对齐
- 🖼️ **图片输出**：列表以图片形式展示，更美观
- 🔄 **自动过期**：临时黑名单到期后自动移除
- 🔒 **出站防护**：Bot 不会向黑名单用户发送任何消息（私聊 + 群聊 @ 均拦截）

## 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `max_blacklist_duration` | int | `86400` | 黑名单最长时长（秒），默认 1 天 |
| `allow_permanent_blacklist` | bool | `true` | 是否允许永久黑名单 |
| `show_blacklist_status` | bool | `true` | 是否向被拉黑用户显示拉黑提示 |
| `blacklist_message` | string | `[连接已中断]` | 拉黑提示内容 |
| `auto_delete_expired_after` | int | `86400` | 过期后多久自动删除记录（秒），`-1` 禁用 |
| `allow_blacklist_admin` | bool | `false` | 是否允许拉黑管理员 |

## 使用方法

### 管理员命令

所有命令需要管理员权限，支持 `/black`、`/bl`、`/blacklist` 前缀。

#### 添加用户到黑名单

```
/black add <用户ID> [时长(秒)] [原因]
```

支持通过 **@ 用户** 代替输入 ID（自动从消息中提取被 @ 者的 ID）。

示例：
```
/black add @user 3600 发送垃圾信息
/black add @user 0 恶意攻击（永久拉黑）
/black add user123           # 使用默认时长
```

#### 从黑名单移除用户

```
/black rm <用户ID>
```

同样支持 @ 用户移除。

#### 查看黑名单列表

```
/black ls [页码] [每页数量]
```

示例：
```
/black ls          # 第 1 页，每页 10 条
/black ls 2 20     # 第 2 页，每页 20 条
```

#### 查看特定用户信息

```
/black info <用户ID>
```

支持 @ 用户查看。

#### 清空黑名单

```
/black clear
```

### LLM 工具

插件注册了 `block_user` 工具，LLM 可在对话中触发拉黑：

- 自动获取当前对话用户的 ID
- 支持自定义时长和原因
- 受 `max_blacklist_duration` 和 `allow_permanent_blacklist` 约束

## 工作原理

1. **入站过滤**（高优先级，`sys.maxsize - 1`）：所有消息先经黑名单检查，被拉黑用户的消息直接被吞掉
2. **出站拦截**（Monkey-patch）：通过 patch `Context.send_message`，在 Bot 向黑名单用户发消息时静默拦截
3. **回复拦截**（patch `event.send`）：阻止任何针对黑名单用户的回复消息
4. **自动过期**：临时黑名单到期后自动失效，可配过期后延迟删除
5. **数据库存储**：所有数据存储在 SQLite 中，重启不丢失

## 鸣谢

- [ctrlkk](https://github.com/ctrlkk) — 原始插件的作者，感谢提供的基础架构和设计思路

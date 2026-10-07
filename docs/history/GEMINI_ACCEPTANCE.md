> [!WARNING]
> **SUPERSEDED HISTORICAL RECORD**: This document is an archived historical specification/record and is no longer active. It has been sanitized for public repository compliance.

# v1.5 AI 消费验收入口

状态：CANDIDATE_NOT_PRODUCTION。候选 v1.5-acceptance-102ff8c，当前生产仍为 v1.1 FINAL。本目录只用于验证 AI 通过 Drive 读取候选；不代表生产已切换。INDEX 中发布状态规则描述的是生产流程，本候选目录没有生产 release_status.json。

所有内容必须从 Drive 文件读取，不需要访问本地路径。请先读 INDEX 和 validation，再按赛季文件或 MASTER.jsonl 查证。不能读取时直接说明失败的文件和原因，不猜测答案。

## 候选文件

- [CBA_注册_2017-2018.md](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)
- [CBA_注册_2018-2019.md](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)
- [CBA_注册_2019-2020.md](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)
- [CBA_注册_2020-2021.md](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)
- [CBA_注册_2021-2022.md](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)
- [CBA_注册_2022-2023.md](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)
- [CBA_注册_2023-2024.md](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)
- [CBA_注册_2024-2025.md](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)
- [CBA_注册_2025-2026.md](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)
- [CBA_注册_2026-2027.md](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)
- [INDEX.md](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)
- [MASTER.csv](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)
- [MASTER.jsonl](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)
- [provenance.json](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)
- [validation.json](https://drive.google.com/file/d/<DRIVE_ID>/view?usp=drivesdk)

## 验收问题

1. 候选编号是什么？这是否表示 v1.5 已经生产上线？
2. 总注册记录数及各赛季计数是什么？这些是否等于独立球员人数？
3. 贾昊在 2024-2025 赛季的公示截止时间、俱乐部和注册方式是什么？提供所在 Drive 文件与原始证据链接。
4. auto_validated 表示什么？空字段能否解释为零或不存在？
5. 任取一条历史赛季记录，提供 record_key、verification_level、source_url；缺失值如实保留。
6. 如果生产发布状态不是 COMPLETE，应如何选择可信数据？当前目录是否包含可验证的生产状态文件？

逐项附实际读取的 Drive 文件链接。无法访问的文件请列出，不以已有聊天记忆或其他版本代替。

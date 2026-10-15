# P10 随仓库技能资源索引

固定目标的 17 个资源目录，共 131 个 Git 跟踪文件；许可证一并保留。这里只说明知识与资源入口，不执行资源脚本。

| 目录 / 正文 | 元数据 name | 描述摘要 | 资源形态与入口 |
| --- | --- | --- | --- |
| [ASR](../skills/ASR/SKILL.md) | ASR | Implement speech-to-text (ASR/automatic speech recognition) capabilities using the z-ai-we… | [scripts](../skills/ASR/scripts)；TypeScript 1 文件 |
| [LLM](../skills/LLM/SKILL.md) | LLM | Implement large language model (LLM) chat completions using the z-ai-web-dev-sdk. Use this… | [scripts](../skills/LLM/scripts)；TypeScript 1 文件 |
| [TTS](../skills/TTS/SKILL.md) | TTS | Implement text-to-speech (TTS) capabilities using the z-ai-web-dev-sdk. Use this skill whe… | TypeScript 1 文件 |
| [VLM](../skills/VLM/SKILL.md) | VLM | Implement vision-based AI chat capabilities using the z-ai-web-dev-sdk. Use this skill whe… | [scripts](../skills/VLM/scripts)；TypeScript 1 文件 |
| [docx](../skills/docx/SKILL.md) | docx | Comprehensive document creation, editing, and analysis with support for tracked changes, c… | [scripts](../skills/docx/scripts)；[ooxml](../skills/docx/ooxml)；Python 12 文件 |
| [finance](../skills/finance/SKILL.md) | finance | Comprehensive Finance API integration skill for real-time and historical financial data an… | 正文及附属资料 |
| [frontend-design](../skills/frontend-design/SKILL.md) | frontend-design | Transform UI style requirements into production-ready frontend code with systematic design… | [examples](../skills/frontend-design/examples)；TypeScript 2 文件；JavaScript 1 文件；[package.json](../skills/frontend-design/package.json) |
| [gift-evaluator](../skills/gift-evaluator/SKILL.md) | gift-evaluator | The PRIMARY tool for Spring Festival gift analysis and social interaction generation. Use … | Python 1 文件 |
| [image-generation](../skills/image-generation/SKILL.md) | image-generation | Implement AI image generation capabilities using the z-ai-web-dev-sdk. Use this skill when… | [scripts](../skills/image-generation/scripts)；TypeScript 1 文件 |
| [pdf](../skills/pdf/SKILL.md) | pdf | Comprehensive PDF manipulation toolkit for extracting text and tables, creating new PDFs, … | [scripts](../skills/pdf/scripts)；Python 10 文件 |
| [podcast-generate](../skills/podcast-generate/SKILL.md) | Podcast Generate | Generate podcast episodes from user-provided content or by searching the web for specified… | TypeScript 1 文件；[package.json](../skills/podcast-generate/package.json) |
| [pptx](../skills/pptx/SKILL.md) | pptx | PPT/PPTX dedicated skill - **MANDATORY RULE**: For ANY PPT-related tasks (uploaded files, … | 正文及附属资料 |
| [video-generation](../skills/video-generation/SKILL.md) | Video Generation | Implement AI-powered video generation capabilities using the z-ai-web-dev-sdk. Use this sk… | [scripts](../skills/video-generation/scripts)；TypeScript 1 文件 |
| [video-understand](../skills/video-understand/SKILL.md) | video-understand | Implement specialized video understanding capabilities using the z-ai-web-dev-sdk. Use thi… | [scripts](../skills/video-understand/scripts)；TypeScript 1 文件 |
| [web-reader](../skills/web-reader/SKILL.md) | web-reader | Implement web page content extraction capabilities using the z-ai-web-dev-sdk. Use this sk… | [scripts](../skills/web-reader/scripts)；TypeScript 1 文件 |
| [web-search](../skills/web-search/SKILL.md) | web-search | Implement web search capabilities using the z-ai-web-dev-sdk. Use this skill when the user… | [scripts](../skills/web-search/scripts)；TypeScript 1 文件 |
| [xlsx](../skills/xlsx/SKILL.md) | xlsx | Comprehensive spreadsheet creation, editing, and analysis with support for formulas, forma… | Python 1 文件 |

已验证：初始正文缓存为 0；17 个正文逐个按需加载成功，无诊断；复制资源逐文件与固定目标比较。资源中的命令、外部 API 与依赖说明保持目标快照，不代表当前外部服务兼容性验证。

阅读时先比较 name 与目录名（如 `Podcast Generate`、`Video Generation`），再查看正文 `$ARGUMENTS`、资源路径和独立依赖入口。SkillTool 使用元数据名称查找，目录名不必相同。

框架加载器不执行这些 Python/TypeScript/JavaScript 文件；要运行资源，需另行准备其声明的运行时、库、外部服务和参数。它们的依赖没有混入本阶段核心 requirements。

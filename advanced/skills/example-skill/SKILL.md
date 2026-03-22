---
name: example-skill
description: 示例 Skill，演示 AgentSkills 规范的目录结构和 SKILL.md 格式。当用户询问如何创建或使用 Skill 时激活此技能。
---

# Example Skill

## 用途
本 Skill 是一个最小化示例，用于验证 Skills 发现与加载机制。

## 使用场景
- 用户询问 Skill 规范或用法
- 需要演示 Skill 激活流程

## 工作方式
1. Agent 启动时自动发现本目录下的 SKILL.md
2. 将 name + description 以 catalog 形式注入 system prompt
3. 当任务匹配 description 时，LLM 调用 `load_skill("example-skill")` 加载本文件
4. 用户也可输入 `/example-skill [问题]` 手动激活

## 步骤
收到相关问题后，简要解释 Skills 机制，并指引用户查看 `advanced/skills/` 目录。

import requests
import json
import hashlib
import time
from datetime import datetime
from typing import Optional, Dict, Any

# 与批处理脚本共用同一份已确认的最终提示词，避免两处内容后续出现偏差。
from run_price_batch import SYSTEM_PROMPT, USER_PROMPT

BASE_API_URL = "https://aismapi.mengniu.cn/brcapi/brain/api/v1/flowApi/45c1d7fe3c?version=dev"
appid = "aism-8I4GX61"
secret = "3005609PIFGO2FAYJL0EASA5V829YK35W32EM5YR64JV35O7"

# 流请求基本参数说明（其他参数为流特定的输入参数）：
# apiVersion 调用的api版本，目前最新版本0.0.2支持多命中多个插件
# sessionId 会话id
# userid 执行人id
# agentTaskId agent任务id，插件二次确认时需传递返回的agentTaskId
# confirm_plugins 插件二次确认，确认的插件id集合

body = {
  "image": "https://ossaism.mengniu.cn/20260508/1ae1a87fb39b878a5536e085d1312fc7/2052571570718773248.jpeg",
  "system_text": SYSTEM_PROMPT,
  "text": USER_PROMPT,
  "userId": "",
  "envSystemName": "",
  "userName": "",
  "envSystemVersion": "",
  "unionId": "",
  "apiVersion": "0.0.2"
}
timestamp = "1789454029859"

class FlowResponseParser:
    """智能体工作流响应解析器"""

    def __init__(self, appid: str, secret: str):
        self.appid = appid
        self.secret = secret

    def make_signature(self, body: dict, timestamp: str) -> str:
        """生成请求签名"""
        sign_str = f"{json.dumps(body)}{self.secret}{timestamp}"
        return hashlib.md5(sign_str.encode('utf-8')).hexdigest()

    def print_separator(self, title: str = "", length: int = 80):
        """打印分隔线"""
        if title:
            padding = (length - len(title) - 2) // 2
            print("=" * padding + f" {title} " + "=" * padding)
        else:
            print("=" * length)

    def safe_get(self, data: dict, key: str, default: Any = ""):
        """安全获取字典值，避免KeyError"""
        return data.get(key, default)

    def parse_basic_info(self, response_data: dict):
        """解析基础信息"""
        print("【基本信息】:")
        status = self.safe_get(response_data, 'status', 0)
        status_text = "成功" if status == 0 else "失败"
        print(f"  状态: {status_text}")
        print(f"  消息: {self.safe_get(response_data, 'message')}")

        payload = self.safe_get(response_data, 'payload', {})
        print(f"  token消耗: {self.safe_get(payload, 'tokenConsume')}")
        print(f"  会话ID: {self.safe_get(payload, 'sessionId')}")
        print(f"  任务ID: {self.safe_get(payload, 'agentTaskId')}")
        print(f"  日志ID: {self.safe_get(payload, 'logId')}")
        print(f"  用户ID: {self.safe_get(payload, 'uid')}")

    def parse_model_info(self, result: dict):
        """解析模型信息"""
        print()
        print("【执行的模型信息】:")
        chosen_model = self.safe_get(result, 'chosen_model')
        created = self.safe_get(result, 'created')

        print(f"  选择的模型名称: {chosen_model}")

        if created:
            try:
                time_str = datetime.fromtimestamp(created).strftime('%Y-%m-%d %H:%M:%S')
                print(f"  模型回复时间: {time_str}")
            except (ValueError, TypeError):
                print(f"  模型回复时间: {created}")

    def parse_final_result(self, result: dict):
        """解析最终结果"""
        print()
        print("【***智能体最终结果***】:")
        page_content = self.safe_get(result, 'page_content')
        print(f"  {page_content}")

    def parse_knowledge_references(self, knowledge_refer: list):
        """解析知识库引用"""
        if not knowledge_refer:
            return

        print()
        print("【表单智能体，知识库文件引用信息】:")
        self.print_separator("", 40)

        print("引用文件:")
        for i, ref in enumerate(knowledge_refer, 1):
            print(f"{i}. 知识库名称: {self.safe_get(ref, 'base_name')}")
            print(f"  知识库编码: {self.safe_get(ref, 'call_code')}")
            print(f"  知识库唯一ID: {self.safe_get(ref, 'id')}")
            print(f"  文件唯一ID: {self.safe_get(ref, 'file_id')}")
            print(f"  文件名: {self.safe_get(ref, 'source')}")
            print(f"  文件访问URL: {self.safe_get(ref, 'file_path')}")

    def parse_user_input(self, data):
        """解析用户输入"""
        print(f"    【用户输入】: {data}")

    def parse_plugin_check(self, data: dict, is_last_step: bool, response_payload: dict):
        """解析插件检查"""
        need_confirm = self.safe_get(data, 'need_confirm', 0)
        check_data = self.safe_get(data, 'check_data', [])

        confirm_text = "是" if need_confirm else "否"
        print(f"    【***插件二次确认***】(需要确认: {confirm_text})")

        for i, check_item in enumerate(check_data, 1):
            print(f"      {i}. {self.safe_get(check_item, 'chineseName')} ({self.safe_get(check_item, 'name')})")
            print(f"         插件call_id: {self.safe_get(check_item, 'id')}")
            print(f"         插件描述: {self.safe_get(check_item, 'description')}")
            print(f"         arguments参数: {self.safe_get(check_item, 'arguments')}")

        if need_confirm and is_last_step:
            # 更新body用于二次确认
            body.update({
                "confirm_plugins": ["call_xxx"],
                "agentTaskId": self.safe_get(response_payload, 'agentTaskId'),
                "sessionId": self.safe_get(response_payload, 'sessionId')
            })

            print(f"    【***插件二次确认入参***】")
            print()
            print(f"         请将智能体任务id agentTaskId和会话id sessionId键值对添加到body里；")
            print(f"         将需要运行的插件id列表body里的confirm_plugins字段进行智能体二次确认运行")
            print(f"         注意：二次确认运行时body里必须传入confirm_plugins字段，一个插件都不执行传入空列表[]即可，如：")
            print()
            print(f"         body = {json.dumps(body, indent=4, ensure_ascii=False)}")

    def parse_plugin_results(self, data: list):
        """解析插件执行结果"""
        print(f"    【插件执行结果】:")

        for j, result_item in enumerate(data, 1):
            status = self.safe_get(result_item, 'status', 0)
            status_text = "成功" if status == 0 else "失败"

            print(
                f"      {j}. 插件名称: {self.safe_get(result_item, 'chineseName')} {self.safe_get(result_item, 'name')}")
            print(f"         插件状态: {status_text}")
            print(f"         插件ID: {self.safe_get(result_item, 'pluginId')}")
            print(f"         插件描述: {self.safe_get(result_item, 'description')}")
            print(f"         插件类型: {self.safe_get(result_item, 'type')}")
            print(f"         插件arguments参数: {self.safe_get(result_item, 'arguments')}")

            # 插件请求信息
            plugin_request = self.safe_get(result_item, 'plugin_request', {})
            print(f"         插件入参: {json.dumps(plugin_request, indent=4, ensure_ascii=False)}")

            # 时间信息
            print(f"         模型function call选择插件时间: {self.safe_get(result_item, 'model_time', 0.0):.2f}s")
            print(f"         插件执行时间: {self.safe_get(result_item, 'plugin_time', 0.0):.2f}s")
            print(f"         总时间: {self.safe_get(result_item, 'total_time', 0.0):.2f}s")

            # 插件响应信息
            plugin_response = self.safe_get(result_item, 'plugin_response', {})
            print(f"         插件出参: {json.dumps(plugin_response, indent=4, ensure_ascii=False)}")

    def parse_chat_response(self, data: dict):
        """解析聊天响应"""
        print(f"    【模型回复】:")
        reasoning_content = self.safe_get(data, 'reasoningContent')
        content = self.safe_get(data, 'content')

        print(f"      ***模型推理过程***：{reasoning_content}")
        print(f"      ***模型回复正文***：{content}")

    def parse_display_items(self, display_items: list, response_payload: dict):
        """解析显示项"""
        if not display_items:
            return

        print()
        print("【表单智能体执行过程】:")

        for i, display_item in enumerate(display_items, 1):
            display_type = self.safe_get(display_item, 'type')
            data = self.safe_get(display_item, 'data')
            is_last_step = (i == len(display_items))

            print(f"  步骤 {i}: {display_type}")

            if display_type == "user":
                self.parse_user_input(data)
            elif display_type == "plugin_check":
                self.parse_plugin_check(data, is_last_step, response_payload)
            elif display_type == "plugin_result":
                self.parse_plugin_results(data)
            elif display_type == "chat":
                self.parse_chat_response(data)
            else:
                print(f"    【未知显示类型】: {display_type}")
                print(f"    数据: {data}")

    def parse_response(self, response_data: dict):
        """解析完整响应"""
        # 获取基本数据
        payload = self.safe_get(response_data, 'payload', {})
        result = self.safe_get(payload, 'result', {})
        agent_type = self.safe_get(result, 'type')

        # 判断智能体类型
        self.print_separator()
        agent_type_name = "表单智能体" if agent_type else "思维链智能体"
        print(f"【AI工作流类型：{agent_type_name}】")

        self.print_separator()
        print("【AI工作流执行结果】")
        self.print_separator()

        # 解析各部分
        self.parse_basic_info(response_data)
        self.parse_model_info(result)  # 只有表单智能体会返回值
        self.parse_final_result(result)

        # 解析知识库引用
        knowledge_refer = self.safe_get(result, 'knowledge_refer', [])
        self.parse_knowledge_references(knowledge_refer)

        # 解析显示项（执行过程） # 只有表单智能体会返回值
        display_items = self.safe_get(result, 'display', [])
        self.parse_display_items(display_items, payload)

        self.print_separator()

    def handle_error_response(self, status_code: int, response_data: dict):
        """处理错误响应"""
        self.print_separator("AI工作流执行失败")
        print(f"HTTP状态码: {status_code}")
        print(f"错误消息: {response_data.get('message', '未知错误')}")

        # 常见错误码说明
        error_descriptions = {
            400: "参数错误",
            401: "认证失败",
            403: "权限不足",
            429: "请求频率限制",
            500: "服务器内部错误",
            502: "网关错误",
            503: "服务不可用",
            504: "请求超时"
        }

        if status_code in error_descriptions:
            print(f"错误类型: {error_descriptions[status_code]}")

        self.print_separator()

    def run_flow(self, url: str, body: dict):
        """执行工作流并解析响应"""
        timestamp = str(int(time.time() * 1000))
        headers = {
            "X-MN-APP-ID": self.appid,
            "X-MN-SIGN": self.make_signature(body, timestamp),
            "X-MN-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

        try:
            print("【系统提示】正在执行AI工作流...")
            response = requests.post(url, headers=headers, json=body)

            if response.status_code == 200:
                data = response.json()
                print("原始响应:")
                print(data)
                print()
                #data为流式返回原始响应字典,后续为响应的解析说明，直接使用返回响应可注释后续代码

                # --- 解析响应 ---
                # 检查业务状态码
                status = self.safe_get(data, 'status', 0)
                if status != 0:
                    print(f"业务执行失败: 状态码 {status}")
                    print(f"错误消息: {self.safe_get(data, 'message')}")
                    return

                # 解析成功响应
                self.parse_response(data)
                # --- 解析响应 ---
                pass

            else:
                # 处理HTTP错误
                try:
                    error_data = response.json()
                except:
                    error_data = {"message": response.text}
                self.handle_error_response(response.status_code, error_data)

        except requests.RequestException as e:
            print(f"网络请求失败: {e}")
        except json.JSONDecodeError as e:
            print(f"JSON解析失败: {e}")
        except Exception as e:
            print(f"未知错误: {e}")


def main():
    """主函数"""
    parser = FlowResponseParser(appid, secret)

    try:
        parser.run_flow(BASE_API_URL, body)
    except KeyboardInterrupt:
        print("用户中断执行")
    except Exception as e:
        print(f"程序异常: {e}")


if __name__ == "__main__":
    main()

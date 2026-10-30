import math
import httpx
from typing import Any

from .reranking import MemoryReranker, MemoryRerankDecision
from .reranking import MemoryReranker, MemoryRerankDecision, InferenceUsage

class HTTPMemoryReranker(MemoryReranker):
    policy_id: str

    def __init__(self, endpoint: str, *, client: Any, batch_size: int = 32, policy_id: str = "http-cross-encoder-v1"):
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.endpoint = endpoint
        self.client = client
        self.batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(self, query: str, candidates: tuple, limit: int) -> MemoryRerankDecision:
        # 1. 参数校验
        if not candidates:
            raise ValueError("No candidates")
        if limit < 1 or limit > len(candidates):
            raise ValueError("Invalid limit")

        # 2. 准备大计分板
        all_scores = {}
        total = len(candidates)
        request_count = 0
        # 3. 边切块边发送（这样 i 就不会丢失）
        for i in range(0, total, self.batch_size):
            chunk = candidates[i : i + self.batch_size]
            candidates_payload = []
            
            for rank, candidate in enumerate(chunk, start=i + 1):
                candidates_payload.append({
                    "rank": rank,
                    "text": candidate.text  # 如果官方叫 content，这里可能会报错，报错再说
                })
            
            # 组装完整请求并发送
            payload = {
                "query": query,
                "candidates": candidates_payload
            }
            response = await self.client.post(self.endpoint, json=payload)
            response.raise_for_status()
            request_count += 1
            # 解析 JSON
            data = response.json()
            #安检
            #[1]
            if set(data.keys())!={"scores"}:
                raise ValueError("Response must contain exactly 'scores'")
            #[2]
            scores_list =data["scores"]
            expected_ranks ={rank for rank,_ in enumerate(chunk,start=i+1)}
            received_ranks =set()
            for item in scores_list:
                if set(item.keys())!={"rank","score"}:
                    raise ValueError("Score item must contain exactly 'rank' and 'score'")
                r =item["rank"]
                s =item["score"]
                #[3]去重
                if r in received_ranks:
                    raise ValueError("Duplicate rank")
                received_ranks.add(r)
                #[4]
                if not math.isfinite(s):
                    raise ValueError("Score must be finite")
                all_scores[r] =s

            #[5]缺失或多加
            if received_ranks!=expected_ranks:
                raise ValueError("Missing or extra ranks in response")


            print("=== 服务器返回的数据是 ===", data)
            
            # 提取分数存入计分板
            for item in data["scores"]:
                r = item["rank"]
                s = item["score"]
                all_scores[r] = s
                
        print("所有分数汇总：", all_scores)
        
        sorted_ranks =sorted(all_scores.keys(),key=lambda rank:(-all_scores[rank],rank))
        final_rank =tuple(sorted_ranks[:limit])
        return MemoryRerankDecision(selected_ranks=final_rank, usage=InferenceUsage(requests=request_count))
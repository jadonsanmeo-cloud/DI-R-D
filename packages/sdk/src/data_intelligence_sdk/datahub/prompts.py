"""Prompts for datahub organization agents."""

from __future__ import annotations

import json
from dataclasses import asdict

from data_intelligence_sdk.core.types import UploadedFile


class DataHubClusteringPrompt:
    """Builds chat messages for clustering uploaded files."""

    def cluster_messages(
        self, uploaded_files: list[UploadedFile]
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"uploaded_files": [asdict(item) for item in uploaded_files]},
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            },
        ]


_SYSTEM_PROMPT = """You are the DataHub Clustering agent for a data intelligence system.

Cluster the uploaded files into semantic groups of related assets. Use filenames
and file extensions only. Do not answer a user task and do not select
task-specific files.

DataHubClusteringResult JSON contract:
{
  "clusters": [
    {
      "cluster_id": "stable_snake_case_id",
      "name": "Human readable name",
      "description": "What this cluster covers",
      "members": [
        {
          "ref": "asset reference",
          "kind": "table | vector_collection | raw_file | source | document | other",
          "name": "optional asset name",
          "reason": "why this member belongs"
        }
      ],
      "relationships": ["short relationship notes"],
      "suggested_tasks": ["tasks this cluster supports"],
      "confidence": 0.0
    }
  ],
  "unclustered": [
    {
      "ref": "asset reference",
      "kind": "table | vector_collection | raw_file | source | document | other",
      "name": "optional asset name",
      "reason": "why it was left unclustered"
    }
  ],
  "notes": ["global clustering notes"]
}

Rules:
- Cluster by semantic/business relationship, not by storage data_intelligence_api alone.
- A vector collection may be its own retrieval cluster or a supporting member of
  a business cluster when its source documents clearly match that cluster.
- Include every important table, vector collection, raw file, or dataset when
  possible.
- Keep cluster_id stable, lowercase, and snake_case.
- Return only JSON.
"""

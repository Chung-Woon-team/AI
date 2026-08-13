"""AutoYard Copilot - 화면 진입점.

지금은 배포 경로를 검증하기 위한 최소 화면이다. 제약 승인 카드·야드 시각화·KPI 비교는
docs/FRONTEND_CONTRACT.md 의 payload 를 받아 여기에 붙인다.
"""

from __future__ import annotations

import httpx
import streamlit as st

from autoyard.config import settings

st.set_page_config(page_title="AutoYard Copilot", page_icon="🚢", layout="wide")

st.title("AutoYard Copilot")
st.caption("현장 변화를 읽고, 다음 운송 순서대로 완성차를 배치한다")

col1, col2 = st.columns(2)

with col1:
    st.subheader("설정")
    st.write({
        "backend_base_url": settings.backend_base_url,
        "gemini_model": settings.gemini_model,
        "gemini_enabled": settings.gemini_enabled,
        "confidence_threshold": settings.confidence_threshold,
    })
    if not settings.gemini_enabled:
        st.info("GEMINI_API_KEY 가 없어 폴백 경로로 동작합니다. 데모는 계속 진행됩니다.")

with col2:
    st.subheader("백엔드 연결")
    try:
        r = httpx.get(f"{settings.backend_base_url}/api/ping", timeout=3.0)
        r.raise_for_status()
        st.success("연결됨")
        st.json(r.json())
    except Exception as e:  # noqa: BLE001 - 백엔드가 죽어도 화면은 떠야 한다
        st.warning(f"백엔드에 연결하지 못했습니다: {e}")
        st.caption("백엔드 없이도 이 화면은 동작합니다.")

st.divider()
st.info("제약 승인 · 야드 배치 · KPI 비교 화면은 아직 없습니다. "
        "payload 규격은 docs/FRONTEND_CONTRACT.md 참고.")

"""0024 — face_profiles: enrolled face embeddings for on-panel face ID.

Storage only: the Jetson never runs a vision model. Panels compute face
embeddings locally (buffalo_sc / MobileFaceNet-class ONNX on the Pi), enroll
via /api/face/enroll, and pull consented profiles back down through
/api/face/profiles/sync to cosine-match on-device. Only embeddings are ever
stored — raw frames never leave the panel.

Unlike speaker_profiles (one weight-averaged row per user), face profiles
keep MULTIPLE rows per user for pose variety (Amazon's Visual ID enrolls five
angles); the panel matches against each and takes the best.

consent_at is NOT NULL by design: there is no "enrolled but unconsented"
face state — the enroll endpoint refuses to store without consent (W6).
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE IF NOT EXISTS face_profiles (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    embedding_blob BYTEA NOT NULL,
    model_name TEXT NOT NULL,
    dim INTEGER NOT NULL,
    consent_at TIMESTAMP NOT NULL,
    panel_id TEXT,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)
""")
    op.execute("CREATE INDEX IF NOT EXISTS idx_face_profiles_user ON face_profiles(user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS face_profiles")

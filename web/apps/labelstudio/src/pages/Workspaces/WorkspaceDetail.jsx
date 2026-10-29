import { Button, Typography, Userpic } from "@humansignal/ui";
import { useCallback, useEffect, useState } from "react";
import { useParams, useHistory } from "react-router-dom";
import { IconPlus, IconTrash } from "@humansignal/icons";
import { useAPI } from "../../providers/ApiProvider";
import { cn } from "../../utils/bem";
import { Spinner } from "../../components";
import { Modal, info } from "../../components/Modal/Modal";
import { Input } from "../../components/Form";
import { Space } from "../../components/Space/Space";

const ROLES = [
  { value: "annotator", label: "Annotator (Student)" },
  { value: "reviewer", label: "Reviewer (TA)" },
  { value: "manager", label: "Manager (Teacher)" },
  { value: "admin", label: "Administrator" },
];

export const WorkspaceDetail = () => {
  const { id } = useParams();
  const api = useAPI();
  const history = useHistory();
  const [workspace, setWorkspace] = useState(null);
  const [members, setMembers] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showInvite, setShowInvite] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("annotator");
  const [inviting, setInviting] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleValue, setTitleValue] = useState("");

  const fetchData = useCallback(async () => {
    setLoading(true);
    const [wsRes, memberRes] = await Promise.all([
      api.callApi("workspace", { params: { pk: id } }),
      api.callApi("workspaceMembers", { params: { pk: id } }),
    ]);
    if (wsRes) setWorkspace(wsRes);
    if (memberRes) setMembers(memberRes.results ?? memberRes);
    setLoading(false);
  }, [api, id]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleArchive = useCallback(async () => {
    await api.callApi("updateWorkspace", {
      params: { pk: id },
      body: { is_archived: true },
    });
    history.push("/workspaces");
  }, [api, id, history]);

  const handleSaveTitle = useCallback(async () => {
    if (!titleValue.trim()) return;
    await api.callApi("updateWorkspace", {
      params: { pk: id },
      body: { title: titleValue.trim() },
    });
    setWorkspace((prev) => ({ ...prev, title: titleValue.trim() }));
    setEditingTitle(false);
  }, [api, id, titleValue]);

  const handleAddMember = useCallback(async () => {
    if (!inviteEmail.trim()) return;
    setInviting(true);
    const res = await api.callApi("createWorkspaceMember", {
      params: { pk: id },
      body: { email: inviteEmail.trim(), role: inviteRole },
    });
    setInviting(false);
    if (res === null) return;
    setShowInvite(false);
    setInviteEmail("");
    setInviteRole("annotator");
    if (res.created_password) {
      info({
        title: "Account created",
        body: (
          <div style={{ lineHeight: 1.9 }}>
            <div>
              Email: <b>{res.user_email}</b>
            </div>
            <div>
              Initial password:{" "}
              <code style={{ padding: "2px 8px", background: "#f3f4f6", borderRadius: 4 }}>{res.created_password}</code>
            </div>
            <Typography size="small" className="text-neutral-content-subtler">
              Share these credentials with the student. They can change the password after logging in.
            </Typography>
          </div>
        ),
      });
    }
    fetchData();
  }, [api, id, inviteEmail, inviteRole, fetchData]);

  const handleRemoveMember = useCallback(
    async (userPk) => {
      await api.callApi("removeWorkspaceMember", {
        params: { pk: id, userPk },
      });
      fetchData();
    },
    [api, id, fetchData],
  );

  const handleUpdateMemberRole = useCallback(
    async (userPk, role) => {
      await api.callApi("updateWorkspaceMember", {
        params: { pk: id, userPk },
        body: { role },
      });
      fetchData();
    },
    [api, id, fetchData],
  );

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 60 }}>
        <Spinner size={36} />
      </div>
    );
  }

  if (!workspace) {
    return (
      <div style={{ textAlign: "center", padding: 60 }}>
        <Typography>Workspace not found.</Typography>
      </div>
    );
  }

  return (
    <div className={cn("workspace-detail").toClassName()}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 24 }}>
        {editingTitle ? (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Input
              name="title"
              value={titleValue}
              onChange={(e) => setTitleValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSaveTitle();
                if (e.key === "Escape") setEditingTitle(false);
              }}
              style={{ width: 300 }}
            />
            <Button look="primary" size="small" onClick={handleSaveTitle}>
              Save
            </Button>
            <Button look="outlined" size="small" variant="neutral" onClick={() => setEditingTitle(false)}>
              Cancel
            </Button>
          </div>
        ) : (
          <>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>{workspace.title}</h1>
            <Button
              look="outlined"
              variant="neutral"
              size="small"
              onClick={() => {
                setTitleValue(workspace.title);
                setEditingTitle(true);
              }}
            >
              Rename
            </Button>
            {workspace.is_archived && (
              <span
                style={{
                  backgroundColor: "#fef3c7",
                  color: "#92400e",
                  padding: "2px 8px",
                  borderRadius: 4,
                  fontSize: 12,
                }}
              >
                Archived
              </span>
            )}
          </>
        )}
      </div>

      <Space spread style={{ marginBottom: 16 }}>
        <Typography size="large" style={{ fontWeight: 600 }}>
          Members ({members?.length ?? 0})
        </Typography>
        <Space>
          {!workspace.is_archived && (
            <Button leading={<IconPlus className="!h-4" />} look="primary" onClick={() => setShowInvite(true)}>
              Add Member
            </Button>
          )}
          <Button look="outlined" variant="negative" onClick={handleArchive} disabled={workspace.is_archived}>
            Archive Workspace
          </Button>
        </Space>
      </Space>

      {members && members.length > 0 ? (
        <div className={cn("workspace-detail").elem("member-list").toClassName()}>
          <div className={cn("workspace-detail").elem("member-header").toClassName()}>
            <div className={cn("workspace-detail").elem("col").mix("user").toClassName()}>User</div>
            <div className={cn("workspace-detail").elem("col").mix("email").toClassName()}>Email</div>
            <div className={cn("workspace-detail").elem("col").mix("role").toClassName()}>Role</div>
            <div className={cn("workspace-detail").elem("col").mix("actions").toClassName()} />
          </div>
          {members.map((m) => (
            <div key={m.id} className={cn("workspace-detail").elem("member-row").toClassName()}>
              <div className={cn("workspace-detail").elem("col").mix("user").toClassName()}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Userpic user={{ id: m.user, email: m.user_email }} style={{ width: 28, height: 28 }} />
                  <Typography>{m.user_name || "—"}</Typography>
                </div>
              </div>
              <div className={cn("workspace-detail").elem("col").mix("email").toClassName()}>
                <Typography size="small">{m.user_email ?? "—"}</Typography>
              </div>
              <div className={cn("workspace-detail").elem("col").mix("role").toClassName()}>
                <select
                  value={m.role ?? "annotator"}
                  onChange={(e) => handleUpdateMemberRole(m.user, e.target.value)}
                  style={{
                    padding: "4px 8px",
                    borderRadius: 6,
                    border: "1px solid #d1d5db",
                    fontSize: 13,
                    background: "#fff",
                  }}
                >
                  {ROLES.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className={cn("workspace-detail").elem("col").mix("actions").toClassName()}>
                <Button
                  look="string"
                  variant="negative"
                  size="small"
                  onClick={() => handleRemoveMember(m.user)}
                  aria-label="Remove member"
                >
                  <IconTrash className="!h-4" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ textAlign: "center", padding: 40 }}>
          <Typography className="text-neutral-content-subtler">No members in this workspace yet.</Typography>
        </div>
      )}

      {showInvite && (
        <Modal
          animateAppearance
          title="Add Member to Workspace"
          style={{ width: 480 }}
          onHide={() => {
            setShowInvite(false);
            setInviteEmail("");
          }}
          bareFooter
          footer={
            <Space spread>
              <Button
                look="outlined"
                variant="neutral"
                onClick={() => {
                  setShowInvite(false);
                  setInviteEmail("");
                }}
              >
                Cancel
              </Button>
              <Button look="primary" onClick={handleAddMember} waiting={inviting} disabled={!inviteEmail.trim()}>
                Add
              </Button>
            </Space>
          }
        >
          <div style={{ padding: "16px 0", display: "flex", flexDirection: "column", gap: 16 }}>
            <div>
              <label style={{ display: "block", marginBottom: 8, fontWeight: 500 }}>Email</label>
              <Input
                name="email"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder="user@example.com"
                style={{ width: "100%" }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleAddMember();
                }}
              />
            </div>
            <div>
              <label style={{ display: "block", marginBottom: 8, fontWeight: 500 }}>Role</label>
              <select
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value)}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  borderRadius: 6,
                  border: "1px solid #d1d5db",
                  fontSize: 14,
                  background: "#fff",
                }}
              >
                {ROLES.map((r) => (
                  <option key={r.value} value={r.value}>
                    {r.label}
                  </option>
                ))}
              </select>
            </div>
            <Typography size="small" className="text-neutral-content-subtler">
              Existing users join directly; new emails get an account created automatically (initial password shown
              after adding).
            </Typography>
          </div>
        </Modal>
      )}
    </div>
  );
};

WorkspaceDetail.title = ({ workspace }) => workspace?.title ?? "Workspace";
WorkspaceDetail.path = "/:id";
WorkspaceDetail.exact = true;

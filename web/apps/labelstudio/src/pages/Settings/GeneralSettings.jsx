import { Badge, Button, Select, Typography, Tooltip } from "@humansignal/ui";
import { useCallback, useContext, useEffect, useState } from "react";
import { IconSpark } from "@humansignal/icons";
import { Form, Input, TextArea } from "../../components/Form";
import { RadioGroup } from "../../components/Form/Elements/RadioGroup/RadioGroup";
import { ProjectContext } from "../../providers/ProjectProvider";
import { useAPI } from "../../providers/ApiProvider";
import { cn } from "../../utils/bem";
import { HeidiTips } from "../../components/HeidiTips/HeidiTips";
import { createURL } from "../../components/HeidiTips/utils";

export const GeneralSettings = () => {
  const { project, fetchProject } = useContext(ProjectContext);
  const api = useAPI();
  const [workspaces, setWorkspaces] = useState([]);

  useEffect(() => {
    api.callApi("workspaces", { params: { page_size: 999 } }).then((res) => {
      if (res) setWorkspaces(res.results ?? res);
    });
  }, [api]);

  const updateProject = useCallback(() => {
    if (project.id) fetchProject(project.id, true);
  }, [project]);

  const colors = ["#FDFDFC", "#FF4C25", "#FF750F", "#ECB800", "#9AC422", "#34988D", "#617ADA", "#CC6FBE"];

  const samplings = [
    { value: "Sequential", label: "Sequential", description: "Tasks are ordered by Task ID" },
    { value: "Uniform", label: "Random", description: "Tasks are chosen with uniform random" },
  ];

  return (
    <div className={cn("general-settings").toClassName()}>
      <div className={cn("general-settings").elem("wrapper").toClassName()}>
        <h1>General Settings</h1>
        <div className={cn("settings-wrapper").toClassName()}>
          <Form action="updateProject" formData={{ ...project }} params={{ pk: project.id }} onSubmit={updateProject}>
            <Form.Row columnCount={1} rowGap="16px">
              <Input name="title" label="Project Name" />

              <TextArea name="description" label="Description" style={{ minHeight: 128 }} />
              <div className={cn("workspace-placeholder").toClassName()}>
                <div className={cn("workspace-placeholder").elem("badge-wrapper").toClassName()}>
                  <div className={cn("workspace-placeholder").elem("title").toClassName()}>Workspace</div>
                </div>
                <Select
                  name="workspace"
                  placeholder="Select a workspace"
                  value={project.workspace ?? ""}
                  options={[
                    { value: "", label: "None (Organization root)" },
                    ...workspaces.map((ws) => ({ value: ws.id, label: ws.title })),
                  ]}
                  onChange={(val) => {
                    api
                      .callApi("updateProject", {
                        params: { pk: project.id },
                        body: { workspace: val ? Number(val) : null },
                      })
                      .then(() => updateProject());
                  }}
                  triggerClassName="!flex-1"
                />
                <Typography size="small" className="my-tight">
                  Organize projects by assigning them to a workspace (department or class).{" "}
                  <a
                    target="_blank"
                    href={createURL(
                      "https://docs.humansignal.com/guide/manage_projects#Create-workspaces-to-organize-projects",
                      {
                        experiment: "project_settings_tip",
                        treatment: "simplify_project_management",
                      },
                    )}
                    rel="noreferrer"
                    className="underline hover:no-underline"
                  >
                    Learn more
                  </a>
                </Typography>
              </div>
              <RadioGroup
                name="assignment_mode"
                label="Task Assignment Mode"
                labelProps={{ size: "large" }}
                value={project.assignment_mode ?? "auto"}
                onChange={(val) => {
                  api
                    .callApi("updateProject", {
                      params: { pk: project.id },
                      body: { assignment_mode: val },
                    })
                    .then(() => updateProject());
                }}
                simple
              >
                <RadioGroup.Button
                  key="auto"
                  value="auto"
                  label="Free-for-all"
                  description="Anyone can pick up any task."
                />
                <RadioGroup.Button
                  key="manual"
                  value="manual"
                  label="Manual assignment"
                  description="Tasks must be explicitly assigned to annotators."
                />
              </RadioGroup>
              <RadioGroup name="color" label="Color" size="large" labelProps={{ size: "large" }}>
                {colors.map((color) => (
                  <RadioGroup.Button key={color} value={color}>
                    <div className={cn("color").toClassName()} style={{ "--background": color }} />
                  </RadioGroup.Button>
                ))}
              </RadioGroup>

              <RadioGroup label="Task Sampling" labelProps={{ size: "large" }} name="sampling" simple>
                {samplings.map(({ value, label, description }) => (
                  <RadioGroup.Button
                    key={value}
                    value={`${value} sampling`}
                    label={`${label} sampling`}
                    description={description}
                  />
                ))}
                <RadioGroup.Button
                  key="uncertainty-sampling"
                  value=""
                  label={
                    <>
                      Uncertainty sampling{" "}
                      <Tooltip title="Available on Label Studio Enterprise">
                        <Badge
                          variant="enterprise"
                          icon={<IconSpark />}
                          size="small"
                          style="ghost"
                          className="ml-tightest"
                        />
                      </Tooltip>
                    </>
                  }
                  disabled
                  description={
                    <>
                      Tasks are chosen according to model uncertainty score (active learning mode).{" "}
                      <a
                        target="_blank"
                        href={createURL("https://docs.humansignal.com/guide/active_learning", {
                          experiment: "project_settings_workspace",
                          treatment: "workspaces",
                        })}
                        rel="noreferrer"
                      >
                        Learn more
                      </a>
                    </>
                  }
                />
              </RadioGroup>
            </Form.Row>

            <Form.Actions>
              <Form.Indicator>
                <span case="success">Saved!</span>
              </Form.Indicator>
              <Button type="submit" className="w-[150px]" aria-label="Save general settings">
                Save
              </Button>
            </Form.Actions>
          </Form>
        </div>
      </div>
      <HeidiTips collection="projectSettings" />
    </div>
  );
};

GeneralSettings.menuItem = "General";
GeneralSettings.path = "/";
GeneralSettings.exact = true;

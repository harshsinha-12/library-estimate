from fastapi import Request

from backend.app.workflows.surveys import SurveyWorkflow


def get_survey_workflow(request: Request) -> SurveyWorkflow:
    return request.app.state.survey_workflow


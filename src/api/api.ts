import qs from 'qs'
import { ServerConnection } from '@jupyterlab/services'
import { requestAPI } from '../handler'
import { IAssignment, Assignment, ICurrentAssignment } from './assignment'
import { IStudent, Student } from './student'
import { ISubmission, Submission } from './submission'
import { ICourse, Course } from './course'
import { IServerSettings, ServerSettings } from './server-settings'
import {
    AssignmentResponse,
    CourseResponse,
    StudentResponse,
    SubmissionResponse,
    ServerSettingsResponse,
} from './api-responses'

export interface GetAssignmentsResponse {
    assignments: IAssignment[] | null
    currentAssignment: ICurrentAssignment | null
}

export interface GetStudentAndCourseResponse {
    student: IStudent
    course: ICourse
}

export interface NotebookFilesResponse {
    notebooks: { [assignmentId: string]: string[] }
}

export async function listNotebookFiles(): Promise<NotebookFilesResponse> {
    const data = await requestAPI<NotebookFilesResponse>(`/notebook_files`, {
        method: 'GET'
    })
    return data
}

export async function getStudentAndCourse(): Promise<GetStudentAndCourseResponse> {
    const { student, course } = await requestAPI<{
        student: StudentResponse
        course: CourseResponse
    }>(`/course_student`, {
        method: 'GET'
    })
    return {
        student: Student.fromResponse(student),
        course: Course.fromResponse(course)
    }
}

export async function getStudentAndCoursePolled(currentValue?: object): Promise<GetStudentAndCourseResponse> {
    const queryString = qs.stringify({ current_value: JSON.stringify(currentValue) })
    const { student, course } = await requestAPI<{
        student: StudentResponse
        course: CourseResponse
    }>(`/course_student/poll?${ queryString }`, {
        method: 'GET'
    })
    return {
        student: Student.fromResponse(student),
        course: Course.fromResponse(course)
    }
}


export async function getAssignments(path: string): Promise<GetAssignmentsResponse> {
    const queryString = qs.stringify({ path })
    const { assignments, current_assignment } = await requestAPI<{
        assignments: AssignmentResponse[] | null
        current_assignment: AssignmentResponse | null
    }>(`/assignments?${ queryString }`, {
        method: 'GET'
    })
    return {
        assignments: assignments ? assignments.map((data) => Assignment.fromResponse(data)) : null,
        currentAssignment: current_assignment ? Assignment.fromResponse(current_assignment) as ICurrentAssignment : null
    }
}

export async function getAssignmentsPolled(path: string, currentValue?: object): Promise<GetAssignmentsResponse> {
    const queryString = qs.stringify({ path, current_value: JSON.stringify(currentValue) })
    const { assignments, current_assignment } = await requestAPI<{
        assignments: AssignmentResponse[] | null
        current_assignment: AssignmentResponse | null
    }>(`/assignments/poll?${ queryString }`, {
        method: 'GET'
    })
    return {
        assignments: assignments ? assignments.map((data) => Assignment.fromResponse(data)) : null,
        currentAssignment: current_assignment ? Assignment.fromResponse(current_assignment) as ICurrentAssignment : null
    }
}

export async function getServerSettings(): Promise<IServerSettings> {
    try {
        const data = await requestAPI<ServerSettingsResponse>('/settings', {
            method: 'GET'
        })
        return ServerSettings.fromResponse(data)
    } catch (e) {
        if (e instanceof ServerConnection.ResponseError) {
            const response = e.response;
            if (response.status === 404) {
                const message =
                    'EduHeLx Submission server extension is unavailable. Please ensure you have installed the ' +
                    'JupyterLab EduHeLx Submission server extension by running: pip install --upgrade eduhelx_jupyterlab_student. ' +
                    'To confirm that the server extension is installed, run: jupyter server extension list.'
                throw new ServerConnection.ResponseError(response, message);
            } else {
                const message = e.message;
                console.error('Failed to get the server extension settings', message);
                throw new ServerConnection.ResponseError(response, message);
            }
        } else {
            throw e;
        }
    }
}

export async function submitAssignment(
    currentPath: string,
    summary: string,
    description?: string
): Promise<void> {
    const res = await requestAPI<void>(`/submit_assignment`, {
        method: 'POST',
        body: JSON.stringify({
            summary,
            description,
            current_path: currentPath
        })
    })
}

export async function cloneStudentRepository(repositoryUrl: string, currentPath: string): Promise<string> {
    const repositoryRootPath = await requestAPI<string>(`/clone_student_repository`, {
        method: 'POST',
        body: JSON.stringify({
            repository_url: repositoryUrl,
            current_path: currentPath
        })
    })
    return repositoryRootPath
}
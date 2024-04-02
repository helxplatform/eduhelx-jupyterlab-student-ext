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
    InstructorResponse,
} from './api-responses'
import { IInstructor, Instructor } from './instructor'
import { addTimezone } from '../utils'



export interface Polled<T> {
    data: T
    rawData: any
}

export interface UpdateAssignmentData {
    new_name?: string | null,
    directory_path?: string | null,
    available_date?: string | null,
    due_date?: string | null
}

export interface GetAssignmentsResponse {
    assignments: IAssignment[] | null
    currentAssignment: ICurrentAssignment | null
}

export interface GetInstructorAndStudentsAndCourseResponse {
    instructor: IInstructor
    students: IStudent[]
    course: ICourse
}

export async function getInstructorAndStudentsAndCourse(): Promise<GetInstructorAndStudentsAndCourseResponse> {
    const { instructor, students, course } = await requestAPI<{
        instructor: InstructorResponse
        students: StudentResponse[]
        course: CourseResponse
    }>(`/course_instructor_students`, {
        method: 'GET'
    })
    return {
        instructor: Instructor.fromResponse(instructor),
        students: students.map((student) => Student.fromResponse(student)),
        course: Course.fromResponse(course)
    }
}

export async function getInstructorAndStudentsAndCoursePolled(currentValue?: object): Promise<Polled<GetInstructorAndStudentsAndCourseResponse>> {
    const queryString = qs.stringify({ current_value: JSON.stringify(currentValue) })
    const { instructor, students, course } = await requestAPI<{
        instructor: InstructorResponse
        students: StudentResponse[]
        course: CourseResponse
    }>(`/course_instructor_students/poll?${ queryString }`, {
        method: 'GET'
    })
    return {
        data: {
            instructor: Instructor.fromResponse(instructor),
            students: students.map((student) => Student.fromResponse(student)),
            course: Course.fromResponse(course)
        },
        rawData: { instructor, students, course }
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

export async function getAssignmentsPolled(path: string, currentValue?: object): Promise<Polled<GetAssignmentsResponse>> {
    const queryString = qs.stringify({ path, current_value: JSON.stringify(currentValue) })
    const { assignments, current_assignment } = await requestAPI<{
        assignments: AssignmentResponse[] | null
        current_assignment: AssignmentResponse | null
    }>(`/assignments/poll?${ queryString }`, {
        method: 'GET'
    })
    return {
        data: {
            assignments: assignments ? assignments.map((data) => Assignment.fromResponse(data)) : null,
            currentAssignment: current_assignment ? Assignment.fromResponse(current_assignment) as ICurrentAssignment : null
        },
        rawData: { assignments, current_assignment }
    }
}

export async function updateAssignment(assignmentName: string, data: UpdateAssignmentData): Promise<void> {
    const queryString = qs.stringify({ name: assignmentName })
    await requestAPI<void>(`/assignments?${ queryString }`, {
        method: 'PATCH',
        body: JSON.stringify(data)
    })
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
                    'JupyterLab EduHeLx Submission server extension by running: pip install --upgrade eduhelx_jupyterlab_prof. ' +
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
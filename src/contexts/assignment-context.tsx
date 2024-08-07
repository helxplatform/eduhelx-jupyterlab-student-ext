import React, { createContext, useContext, ReactNode, useState, useMemo, useEffect } from 'react'
import { IChangedArgs, URLExt } from '@jupyterlab/coreutils'
import { ServerConnection } from '@jupyterlab/services'
import { FileBrowserModel, IDefaultFileBrowser } from '@jupyterlab/filebrowser'
import { showDialog, Dialog } from '@jupyterlab/apputils'
import { Button } from '@jupyterlab/ui-components'
import { useSnackbar } from './snackbar-context'
import { IEduhelxSubmissionModel } from '../tokens'
import { IAssignment, IStudent, ICurrentAssignment, ICourse, getAssignmentsPolled, GetAssignmentsResponse, getStudentAndCoursePolled, getStudentAndCourse, getAssignments } from '../api'

interface IAssignmentContext {
    assignments: IAssignment[] | null | undefined
    assignment: ICurrentAssignment | null | undefined
    student: IStudent | undefined
    course: ICourse | undefined
    path: string | null
    loading: boolean
}

interface IAssignmentProviderProps {
    fileBrowser: IDefaultFileBrowser
    children?: ReactNode
}

const _SC = ServerConnection.makeSettings()
const WEBSOCKET_URL = URLExt.join(
    _SC.baseUrl,
    "eduhelx-jupyterlab-student",
    "ws"
)
const WEBSOCKET_REOPEN_DELAY = 1000

export const AssignmentContext = createContext<IAssignmentContext|undefined>(undefined)

export const AssignmentProvider = ({ fileBrowser, children }: IAssignmentProviderProps) => {
    const snackbar = useSnackbar()!

    const [currentPath, setCurrentPath] = useState<string|null>(null)
    const [currentAssignment, setCurrentAssignment] = useState<ICurrentAssignment|null|undefined>(undefined)
    const [assignments, setAssignments] = useState<IAssignment[]|null|undefined>(undefined)
    const [student, setStudent] = useState<IStudent|undefined>(undefined)
    const [course, setCourse] = useState<ICourse|undefined>(undefined)
    const [ws, setWs] = useState<WebSocket>(() => new WebSocket(WEBSOCKET_URL))

    const loading = useMemo(() => (
        currentAssignment === undefined ||
        assignments === undefined ||
        student === undefined ||
        course === undefined
    ), [currentAssignment, assignments, student, course])

    useEffect(() => {
        const triggerReconnect = () => {
            ws.close()
            setWs(new WebSocket(WEBSOCKET_URL))
        }

        ws.addEventListener("message", (e) => {
            const { type, ...data } = JSON.parse(e.data)
            if (type === "downsync") showDialog({
                title: "New files have been added",
                body: (
                    <ul>
                        { data.files.map((f: string) => (
                            <li>{ f }</li>
                        )) }
                    </ul>
                ),
                buttons: [Dialog.okButton({ label: "Ok" })]
            })
        })
        ws.addEventListener("close", triggerReconnect)
        return () => {
            ws.close()
        }
    }, [ws])

    useEffect(() => {
        setCurrentPath(fileBrowser.model.path)

        const onCurrentPathChanged = (model: FileBrowserModel, change: IChangedArgs<string|null>) => {
            setCurrentPath(change.newValue)
        }
        fileBrowser.model.pathChanged.connect(onCurrentPathChanged)
        return () => {
            fileBrowser.model.pathChanged.disconnect(onCurrentPathChanged)
        }
    }, [fileBrowser])

    useEffect(() => {
        setAssignments(undefined)
        setCurrentAssignment(undefined)
        
        let cancelled = false
        const poll = async () => {
            let value
            if (currentPath !== null) {
                try {
                    value = await getAssignments(currentPath)
                } catch (e: any) {
                    console.error(e)
                    snackbar.open({
                        type: 'warning',
                        message: 'Failed to pull assignments...'
                    })
                }
            }
            if (cancelled) return
            if (value !== undefined) {
                setAssignments(value.assignments)
                setCurrentAssignment(value.currentAssignment)
            } else {
                setAssignments(undefined)
                setCurrentAssignment(undefined)
            }
            setTimeout(poll, 2500)
        }
        setTimeout(poll, 2500)
        return () => {
            cancelled = true
        }
    }, [currentPath])

    useEffect(() => {
        setCourse(undefined)
        setStudent(undefined)

        let cancelled = false
        const poll = async () => {
            let value
            try {
                value = await getStudentAndCourse()
            } catch (e: any) {
                console.error(e)
                snackbar.open({
                    type: 'warning',
                    message: 'Failed to pull course data...'
                })
            }
            if (cancelled) return
            if (value !== undefined) {
                setCourse(value.course)
                setStudent(value.student)
            } else {
                setCourse(undefined)
                setStudent(undefined)
            }
            setTimeout(poll, 2500)
        }
        setTimeout(poll, 2500)
        return () => {
            cancelled = true
        }
    }, [])

    return (
        <AssignmentContext.Provider value={{
            assignment: currentAssignment,
            assignments,
            student,
            course,
            path: currentPath,
            loading
        }}>
            { children }
        </AssignmentContext.Provider>
    )
}
export const useAssignment = () => useContext(AssignmentContext)
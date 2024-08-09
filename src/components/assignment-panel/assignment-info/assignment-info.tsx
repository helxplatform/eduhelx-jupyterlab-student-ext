import React, { Fragment, useMemo } from 'react'
import { Tooltip } from 'antd'
import moment from 'moment'
import pluralize from 'pluralize'
import { ArrowBackSharp } from '@material-ui/icons'
import { assignmentInfoClass, assignmentInfoSectionClass, assignmentInfoSectionHeaderClass, assignmentInfoSectionWarningClass, assignmentNameClass, tagClass } from './style'
import { useAssignment } from '../../../contexts'
import { DateFormat } from '../../../utils'

const MS_IN_HOURS = 3.6e6

interface AssignmentInfoProps {
}

export const AssignmentInfo = ({  }: AssignmentInfoProps) => {
    const { assignment, student, course } = useAssignment()!
    if (!student || !assignment || !course) return null

    const hoursUntilDue = useMemo(() => (
        assignment.isPublished ? (
            (assignment.adjustedDueDate!.getTime() - Date.now()) / MS_IN_HOURS
        ) : Infinity
    ), [assignment])

    const assignmentStatusTag = useMemo(() => {
        let color = undefined
        let backgroundColor = undefined
        let borderColor = undefined
        let text = undefined
        let tooltip = undefined
        let filled = false
        if (!assignment.isPublished || !assignment.isAvailable) {
            // Upcoming assignment (missing either an open/close date OR open date not reached yet)
            color = "white"
            backgroundColor = "#1890ff"
            text = "Upcoming"
            tooltip = `Your ${ pluralize("instructor", course.instructors.length) } has not released this assignment yet`
            filled = true
        }
        else if (assignment.isClosed) {
            // Not available to work on anymore
            if (assignment.activeSubmission) {
                // Closed and submitted
                color = "var(--jp-success-color1)"
                backgroundColor = "var(--jp-success-color1)"
                text = (
                    <span>
                        Submitted { new DateFormat(assignment.activeSubmission.submissionTime).toRelativeDatetimeNoArticle() } ago
                    </span>
                )
                tooltip = `You submitted this assignment before it closed. Please contact your ${ pluralize("instructor", course.instructors.length) } if you need to resubmit`
                filled = false
            } else {
                // Closed and never submitted
                color = "var(--jp-error-color1)"
                backgroundColor = "var(--jp-error-color1)"
                text = (
                    <span>
                        { new DateFormat(assignment.adjustedDueDate!).toRelativeDatetimeNoArticle() } past due
                    </span>
                )
                tooltip = `You never submitted this assignment. Please contact your ${ pluralize("instructor", course.instructors.length) } to request an extension`
                filled = false
            }            
        } else if (assignment.isAvailable) {
            // Available
            color = assignment.activeSubmission ? "var(--jp-success-color1)" : "var(--jp-ui-font-color1)"
            backgroundColor = assignment.activeSubmission ? "var(--jp-success-color1)" : "#fafafa"
            borderColor = assignment.activeSubmission ? undefined : "#d9d9d9"
            text = assignment.activeSubmission ? "Submitted" : "Not Submitted"
            tooltip = assignment.activeSubmission ? `You have submitted this assignment` : `You haven't submitted this assignment yet`
            filled = !assignment.activeSubmission
        }
        return (
            <Tooltip title={ tooltip } placement="right">
                <span
                    className={ tagClass }
                    style={{
                        marginTop: 8,
                        color,
                        backgroundColor: filled ? backgroundColor: "transparent",
                        border: `1px solid ${ borderColor ?? backgroundColor }`,
                        textTransform: "capitalize"
                    }}
                >
                    { text }
                </span>
            </Tooltip>
        )
    }, [course, assignment, hoursUntilDue])
    
    const attemptsDescriptionText = useMemo(() => {
        if (assignment.maxAttempts === null) return "This assignment has unlimited attempts"

        const remainingAttempts = assignment.maxAttempts - assignment.currentAttempts
        const maxAttemptsReached = assignment.currentAttempts === assignment.maxAttempts

        if (maxAttemptsReached) return "You've reached the maximum number of submissions."
        else return `You are allowed to submit ${ remainingAttempts }${ assignment.currentAttempts > 0 ? ' more' : ''} ${ pluralize("time", remainingAttempts) }`
    }, [assignment])

    return (
        <div className={ assignmentInfoClass }>
            <div>
                <header className={ assignmentNameClass }>{ assignment.name }</header>
                { assignmentStatusTag }
                { assignment.isPublished && !assignment.isClosed && hoursUntilDue <= 4 && (
                    <span
                        className={ tagClass }
                        style={{
                            marginTop: 8,
                            marginLeft: 6,
                            color: "white",
                            backgroundColor: "var(--jp-warn-color-normal)",
                            border: `1px solid var(--jp-warn-color-normal)`,
                            textTransform: "capitalize"
                        }}
                        title={ new DateFormat(assignment.adjustedDueDate!).toBasicDatetime() }
                    >
                        Due in { new DateFormat(assignment.adjustedDueDate!).toRelativeDatetimeNoArticle() }
                    </span>
                ) }
            </div>
            <div className={ assignmentInfoSectionClass } style={{ marginTop: 16 }}>
                <h5 className={ assignmentInfoSectionHeaderClass }>Student</h5>
                <span>{ student.name }</span>
            </div>
            <div className={ assignmentInfoSectionClass }>
                <h5 className={ assignmentInfoSectionHeaderClass }>
                    { pluralize("Instructor", course.instructors.length) }
                </h5>
                <span>{ course.instructors.map((ins) => ins.name).join(", ") }</span>
            </div>
            <div className={ assignmentInfoSectionClass }>
                <h5 className={ assignmentInfoSectionHeaderClass }>Due date</h5>
                <div>
                    { assignment.isPublished ? (
                        new DateFormat(assignment.adjustedDueDate!).toBasicDatetime()
                    ) : (
                        `To be determined`
                    ) }
                    { assignment.isExtended ? (
                        <i>&nbsp;(extended)</i>
                    ) : null }
                </div>
            </div>
            { assignment.maxAttempts !== null && (
                <div className={ assignmentInfoSectionClass }>
                    <h5 className={ assignmentInfoSectionHeaderClass }>
                        Attempts{ assignment.currentAttempts > 0 ? ` (${ assignment.currentAttempts }/${ assignment.maxAttempts })` : "" }
                    </h5>
                    <div>
                        { attemptsDescriptionText }
                    </div>
                </div>
            ) }
            { assignment.isPublished && assignment.isClosed && (
                <div className={ assignmentInfoSectionClass }>
                    <h5
                        className={ `${ assignmentInfoSectionHeaderClass} ${ assignmentInfoSectionWarningClass }` }
                    >
                        Assignment is past due
                    </h5>
                    <div className={ assignmentInfoSectionWarningClass }>
                        No further changes can be submitted.
                        Please contact your {pluralize("instructor", course.instructors.length)} for an extension.
                    </div>
                </div>
            ) }
        </div>
    )
}
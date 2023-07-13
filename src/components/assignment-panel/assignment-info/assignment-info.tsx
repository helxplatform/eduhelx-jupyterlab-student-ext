import React from 'react'
import { assignmentInfoClass, assignmentInfoSectionClass, assignmentInfoSectionHeaderClass, assignmentNameClass } from './style'
import { useAssignment } from '../../../contexts'
import { DateFormat } from '../../../utils'

export const AssignmentInfo = () => {
    const { assignment, student } = useAssignment()!
    if (!student || !assignment) return null
    return (
        <div className={ assignmentInfoClass }>
            <header className={ assignmentNameClass }>{ assignment.name }</header>
            <div className={ assignmentInfoSectionClass }>
                <h5 className={ assignmentInfoSectionHeaderClass }>Student</h5>
                <span>{ student.firstName } { student.lastName }</span>
            </div>
            <div className={ assignmentInfoSectionClass }>
                <h5 className={ assignmentInfoSectionHeaderClass }>Professor</h5>
                <span>{ student.professorOnyen }</span>
            </div>
            <div className={ assignmentInfoSectionClass }>
                <h5 className={ assignmentInfoSectionHeaderClass }>Due date</h5>
                <span>{ new DateFormat(assignment.dueDate).toBasicDatetime() }</span>
            </div>
        </div>
    )
}
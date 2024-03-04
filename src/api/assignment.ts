import { IStudent, Student } from './student'
import { ISubmission, Submission } from './submission'
import { AssignmentResponse } from './api-responses'

export interface IAssignment {
    readonly id: number
    readonly name: string
    readonly directoryPath: string
    // Added by serverextension so that we know what path to give the filebrowser
    // to open an assignment, even though we don't know where the repo root is.
    readonly absoluteDirectoryPath: string
    readonly createdDate: Date
    readonly adjustedAvailableDate: Date | null
    readonly adjustedDueDate: Date | null
    readonly lastModifiedDate: Date

    // Indicates that release date has been deferred to a later date for the student
    readonly isDeferred: boolean
    // Indicates that due date is extended to a later date for the student
    readonly isExtended: boolean
    // Indicates if an assignment has an available_date and a due_date assigned to it.
    readonly isCreated: boolean
    // Indicates if an assignment is available to work on (e.g. date is greater than adjusted_available_date)
    readonly isAvailable: boolean
    // Indicates if an assignment is no longer available to work on (e.g. date is greater than adjusted_due_date)
    readonly isClosed: boolean
    readonly submissions?: ISubmission[]
    // `null` if there are no submissions
    readonly activeSubmission?: ISubmission | null
}

// Submissions are definitely defined in an ICurrentAssignment
export interface ICurrentAssignment extends IAssignment {
    readonly submissions: ISubmission[]
    // `null` if there are no submissions
    readonly activeSubmission: ISubmission | null
}

export class Assignment implements IAssignment {
    constructor(
        private _id: number,
        private _name: string,
        private _directoryPath: string,
        private _absoluteDirectoryPath: string,
        private _createdDate: Date,
        private _adjustedAvailableDate: Date | null,
        private _adjustedDueDate: Date | null,
        private _lastModifiedDate: Date,

        private _isDeferred: boolean,
        private _isExtended: boolean,
        private _isCreated: boolean,
        private _isAvailable: boolean,
        private _isClosed: boolean,
        private _submissions?: ISubmission[]
    ) {}
    
    get id() { return this._id }
    get name() { return this._name }
    get directoryPath() { return this._directoryPath }
    get absoluteDirectoryPath() { return this._absoluteDirectoryPath }
    get createdDate() { return this._createdDate }
    get adjustedAvailableDate() { return this._adjustedAvailableDate }
    get adjustedDueDate() { return this._adjustedDueDate }
    get lastModifiedDate() { return this._lastModifiedDate }
    

    get isDeferred() { return this._isDeferred }
    get isExtended() { return this._isExtended }
    get isCreated() { return this._isCreated }
    get isAvailable() { return this._isAvailable }
    get isClosed() { return this._isClosed }

    get submissions() { return this._submissions }
    get activeSubmission() {
        if (this._submissions === undefined) return undefined
        if (this._submissions.length === 0) return null
        return this._submissions.sort((a, b) => b.submissionTime.getTime() - a.submissionTime.getTime())[0]
    }
    

    static fromResponse(data: AssignmentResponse): IAssignment {
        return new Assignment(
            data.id,
            data.name,
            data.directory_path,
            data.absolute_directory_path,
            new Date(data.created_date),
            data.adjusted_available_date ? new Date(data.adjusted_available_date) : null,
            data.adjusted_due_date ? new Date(data.adjusted_due_date) : null,
            new Date(data.last_modified_date),

            data.is_deferred,
            data.is_extended,
            data.is_created,
            data.is_available,
            data.is_closed,
            data.submissions?.map((res) => Submission.fromResponse(res))
        )
    }
}